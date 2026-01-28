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


class FakeWallet:
    def __init__(self, confirm_responses):
        self.confirm_responses = list(confirm_responses)
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
        return 10.0


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
        mint="SomeMint", trade_amount_sol=1.0, base_slippage_bps=100
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
        mint="SomeMint", trade_amount_sol=1.0, base_slippage_bps=120
    )
    # 120 * 10 = 1200 but should cap at 500
    assert result == 500


@pytest.mark.asyncio
async def test_calculate_dynamic_slippage_fallback_on_quote_failure():
    wallet = FakeWallet(confirm_responses=[True])
    engine = TradingEngine(wallet)
    engine.jupiter = FakeJupiter(quote=None)

    result = await engine.calculate_dynamic_slippage(
        mint="SomeMint", trade_amount_sol=1.0, base_slippage_bps=90
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

    result = await engine.buy(mint="SomeMint", amount_sol=0.1, max_retries=3)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert wallet.confirm_calls == 2
    assert wallet.clear_calls == 1


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
    async def fake_dynamic_slippage(mint, trade_amount_sol, base_slippage_bps=100):
        return 150

    engine.calculate_dynamic_slippage = fake_dynamic_slippage
    # Avoid background tasks executing real logic
    async def noop_convert():
        return None

    engine.convert_profit_to_usdc = noop_convert

    result = await engine.sell(mint="SomeMint", amount=1_000_000_000, slippage_bps=None, max_retries=2)

    assert result["success"] is True
    assert fake_jupiter.execute_calls[0]["slippage_bps"] == 150
    assert wallet.clear_calls == 1

