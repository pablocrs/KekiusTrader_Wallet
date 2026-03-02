import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.wallet_info import WalletInfo


class FakeWallet:
    def __init__(self):
        self.raise_tokens = False

    def get_public_key_str(self):
        return "FakeWalletPubKey"

    async def get_sol_balance(self, use_cache=True):
        return 12.34

    async def get_token_accounts(self, force_refresh=False):
        if self.raise_tokens:
            raise RuntimeError("token fetch failed")
        return [
            {
                "pubkey": "token-account-1",
                "mint": "MintA",
                "balance": 100,
                "ui_amount": 1.0,
                "decimals": 2,
                "symbol": "TOKA",
                "name": "Token A",
            }
        ]

    async def get_token_balance(self, mint: str):
        if mint == "ERR":
            raise RuntimeError("balance lookup failed")
        return {"mint": mint, "ui_amount": 1.23, "balance": 123}


@pytest.mark.asyncio
async def test_get_full_wallet_info_success():
    wallet = FakeWallet()
    info = WalletInfo(wallet)

    result = await info.get_full_wallet_info()
    assert result["address"] == "FakeWalletPubKey"
    assert result["sol_balance"] == 12.34
    assert result["token_count"] == 1
    assert len(result["tokens"]) == 1
    assert result["tokens"][0]["mint"] == "MintA"


@pytest.mark.asyncio
async def test_get_full_wallet_info_handles_token_fetch_error():
    wallet = FakeWallet()
    wallet.raise_tokens = True
    info = WalletInfo(wallet)

    result = await info.get_full_wallet_info()
    assert result["token_count"] == 0
    assert result["tokens"] == []


@pytest.mark.asyncio
async def test_get_balance_summary_success():
    wallet = FakeWallet()
    info = WalletInfo(wallet)
    result = await info.get_balance_summary()
    assert result["address"] == "FakeWalletPubKey"
    assert result["sol_balance"] == 12.34


@pytest.mark.asyncio
async def test_get_token_balance_info_success_and_error():
    wallet = FakeWallet()
    info = WalletInfo(wallet)

    ok = await info.get_token_balance_info("MintA")
    assert ok["mint"] == "MintA"
    assert ok["ui_amount"] == 1.23

    err = await info.get_token_balance_info("ERR")
    assert err["mint"] == "ERR"
    assert "error" in err
