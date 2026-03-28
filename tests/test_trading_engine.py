import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.trading_engine import TradingEngine
from config import settings
from core.wallet_client import SOL_MINT

TEST_TOKEN_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


class FakeWallet:
    def __init__(
        self,
        confirm_responses,
        sol_balance=10.0,
        token_balance=2_000_000_000,
        token_ui_amount=1.0,
        usdc_ui_balance=1_000.0,
    ):
        self.confirm_responses = list(confirm_responses)
        self.sol_balance = sol_balance
        self.token_balance = token_balance
        self.token_ui_amount = token_ui_amount
        self.usdc_ui_balance = usdc_ui_balance
        self.send_calls = 0
        self.confirm_calls = 0
        self.clear_calls = 0

    def get_public_key_str(self):
        return "FakePubKey"

    async def send_transaction(self, transaction_b64, skip_preflight=True):
        self.send_calls += 1
        # return unique signature per call
        return f"sig-{self.send_calls}"

    async def confirm_transaction(self, signature, timeout=60):
        self.confirm_calls += 1
        if not self.confirm_responses:
            return True
        response = self.confirm_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def clear_cache(self):
        self.clear_calls += 1

    async def get_sol_balance(self, use_cache=True):
        return self.sol_balance

    async def get_token_balance(self, mint: str):
        if mint == settings.USDC_MINT:
            return {
                "mint": mint,
                "balance": int(self.usdc_ui_balance * 1_000_000),
                "decimals": 6,
                "ui_amount": self.usdc_ui_balance,
            }
        return {"mint": mint, "balance": self.token_balance, "ui_amount": self.token_ui_amount}


class FakeWalletReconcileBuy(FakeWallet):
    async def confirm_transaction(self, signature, timeout=60):
        self.confirm_calls += 1
        # Simulate a tx that lands on-chain while confirmation endpoint times out.
        self.token_balance = 123_000
        return False


class FakeWalletReconcileSell(FakeWallet):
    async def confirm_transaction(self, signature, timeout=60):
        self.confirm_calls += 1
        # Simulate a tx that lands on-chain while confirmation endpoint times out.
        self.token_balance = 0
        return False


class FakeJupiter:
    def __init__(self, quote=None, swap=None):
        self.quote = quote
        self.swap = swap
        self.execute_calls = []

    async def get_quote(self, input_mint, output_mint, amount, slippage_bps=100):
        return self.quote

    async def execute_swap(
        self,
        input_mint,
        output_mint,
        amount,
        user_public_key,
        slippage_bps,
        priority_fee=None,
    ):
        self.execute_calls.append(
            {
                "input_mint": input_mint,
                "output_mint": output_mint,
                "amount": amount,
                "slippage_bps": slippage_bps,
                "priority_fee": priority_fee,
            }
        )
        return self.swap


@pytest.fixture(autouse=True)
def set_settings_defaults(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_SLIPPAGE_BPS", 100, raising=False)
    monkeypatch.setattr(settings, "DEFAULT_SPEED_MODE", "BALANCED", raising=False)
    monkeypatch.setattr(settings, "PRIORITY_FEE_ULTRA_FAST", 0, raising=False)
    monkeypatch.setattr(settings, "PRIORITY_FEE_BALANCED", 0, raising=False)
    monkeypatch.setattr(settings, "PRIORITY_FEE_SAFE", 0, raising=False)
    monkeypatch.setattr(settings, "ENABLE_AUTO_PROFIT_CONVERSION", False, raising=False)
    monkeypatch.setattr(settings, "PROFIT_THRESHOLD_SOL", 0, raising=False)
    yield


@pytest.mark.asyncio
async def test_calculate_dynamic_slippage_base_when_low_impact():
    wallet = FakeWallet(confirm_responses=[True])
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(
        quote={"priceImpactPct": 0.5},
        swap={"transaction": "tx", "output_amount": 1, "price_impact": 0.5},
    )

    result = await engine.calculate_dynamic_slippage(
        mint=TEST_TOKEN_MINT, trade_amount_sol=1.0, base_slippage_bps=100
    )
    assert result == 100


@pytest.mark.asyncio
async def test_calculate_dynamic_slippage_scales_and_caps():
    wallet = FakeWallet(confirm_responses=[True])
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(
        quote={"priceImpactPct": 10.0},
        swap={"transaction": "tx", "output_amount": 1, "price_impact": 10.0},
    )

    result = await engine.calculate_dynamic_slippage(
        mint=TEST_TOKEN_MINT, trade_amount_sol=1.0, base_slippage_bps=120
    )
    # 120 * 10 = 1200 but should cap at 500
    assert result == 500


@pytest.mark.asyncio
async def test_calculate_dynamic_slippage_fallback_on_quote_failure():
    wallet = FakeWallet(confirm_responses=[True])
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(quote=None)

    result = await engine.calculate_dynamic_slippage(
        mint=TEST_TOKEN_MINT, trade_amount_sol=1.0, base_slippage_bps=90
    )
    # 2x base with cap
    assert result == 180


@pytest.mark.asyncio
async def test_buy_retries_on_timeout_then_succeeds(monkeypatch):
    # First confirmation returns False (treated as timeout), second True
    wallet = FakeWallet(confirm_responses=[False, True])
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(
        swap={"transaction": "tx", "output_amount": 123, "price_impact": 0.5}
    )
    # Speed up backoff
    async def fast_sleep(*args, **kwargs):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast_sleep, raising=False)

    result = await engine.buy(mint=TEST_TOKEN_MINT, amount_sol=0.1, max_retries=3)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert wallet.confirm_calls == 2
    assert wallet.clear_calls >= 1


@pytest.mark.asyncio
async def test_sell_uses_dynamic_slippage_when_missing(monkeypatch):
    wallet = FakeWallet(confirm_responses=[True])
    engine = TradingEngine(wallet)
    fake_jupiter = FakeJupiter(
        quote={"outAmount": int(2 * 1e9)},  # used for estimating trade size
        swap={"transaction": "tx", "output_amount": int(1.5 * 1e9), "price_impact": 0.5},
    )
    engine.jupiter = fake_jupiter

    # Stub dynamic slippage calculation to ensure it is used
    async def fake_dynamic_slippage(input_mint, output_mint, amount, base_slippage_bps=100):
        assert input_mint == TEST_TOKEN_MINT
        assert output_mint == SOL_MINT
        assert amount == 1_000_000_000
        return 150

    engine.calculate_dynamic_slippage_for_route = fake_dynamic_slippage
    # Avoid background tasks executing real logic
    async def noop_convert():
        return None

    engine.convert_profit_to_usdc = noop_convert

    result = await engine.sell(mint=TEST_TOKEN_MINT, amount=1_000_000_000, slippage_bps=None, max_retries=2)

    assert result["success"] is True
    assert fake_jupiter.execute_calls[0]["slippage_bps"] == 150
    assert wallet.clear_calls >= 1


@pytest.mark.asyncio
async def test_buy_fails_when_insufficient_sol_balance():
    wallet = FakeWallet(confirm_responses=[True], sol_balance=0.001)
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter()

    result = await engine.buy(mint=TEST_TOKEN_MINT, amount_sol=0.01, max_retries=1)
    assert result["success"] is False
    assert "Insufficient SOL balance" in result["error"]


@pytest.mark.asyncio
async def test_buy_with_usdc_uses_usdc_input_route():
    wallet = FakeWallet(confirm_responses=[True], sol_balance=0.02, usdc_ui_balance=100.0)
    engine = TradingEngine(wallet)
    fake_jupiter = FakeJupiter(
        swap={"transaction": "tx", "output_amount": 123, "price_impact": 0.5}
    )
    engine.jupiter = fake_jupiter

    result = await engine.buy(
        mint=TEST_TOKEN_MINT,
        amount=12.345678,
        spend_denom="USDC",
        max_retries=1,
    )

    assert result["success"] is True
    assert result["input_denom"] == "USDC"
    assert fake_jupiter.execute_calls[0]["input_mint"] == settings.USDC_MINT
    assert fake_jupiter.execute_calls[0]["amount"] == 12_345_678


@pytest.mark.asyncio
async def test_buy_with_usdc_fails_when_insufficient_usdc_balance():
    wallet = FakeWallet(confirm_responses=[True], sol_balance=0.02, usdc_ui_balance=0.5)
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter()

    result = await engine.buy(
        mint=TEST_TOKEN_MINT,
        amount=1.0,
        spend_denom="USDC",
        max_retries=1,
    )
    assert result["success"] is False
    assert "Insufficient USDC balance" in result["error"]


@pytest.mark.asyncio
async def test_buy_timeout_reconciles_as_success(monkeypatch):
    wallet = FakeWalletReconcileBuy(confirm_responses=[], token_balance=0)
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(
        swap={"transaction": "tx", "output_amount": 123_000, "price_impact": 0.5}
    )

    async def fast_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep, raising=False)

    result = await engine.buy(mint=TEST_TOKEN_MINT, amount_sol=0.1, max_retries=1)

    assert result["success"] is True
    assert result["status"] == "confirmed_via_reconciliation"
    assert result["reconciliation"]["token_delta"] > 0


@pytest.mark.asyncio
async def test_sell_fails_when_insufficient_token_balance():
    wallet = FakeWallet(confirm_responses=[True], token_balance=10)
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter()

    result = await engine.sell(mint=TEST_TOKEN_MINT, amount=1000, max_retries=1)
    assert result["success"] is False
    assert "Insufficient token balance" in result["error"]


@pytest.mark.asyncio
async def test_sell_timeout_reconciles_as_success(monkeypatch):
    wallet = FakeWalletReconcileSell(confirm_responses=[], token_balance=1_000)
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(
        swap={"transaction": "tx", "output_amount": int(0.01 * 1e9), "price_impact": 0.5}
    )

    async def fast_sleep(*args, **kwargs):
        return None

    async def noop_convert():
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep, raising=False)
    engine.convert_profit_to_usdc = noop_convert

    result = await engine.sell(mint=TEST_TOKEN_MINT, amount=1_000, max_retries=1)

    assert result["success"] is True
    assert result["status"] == "confirmed_via_reconciliation"
    assert result["reconciliation"]["token_delta"] > 0
