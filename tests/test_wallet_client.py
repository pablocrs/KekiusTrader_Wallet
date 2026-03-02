import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from solders.pubkey import Pubkey

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.wallet_client import WalletClient, SOL_MINT

TEST_TOKEN_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class FakeRpcClient:
    def __init__(self):
        self.balance_calls = 0
        self.token_owner_calls = 0
        self.status_calls = 0

    async def get_balance(self, pubkey):
        self.balance_calls += 1
        return SimpleNamespace(value=2_000_000_000)  # 2 SOL

    async def get_token_accounts_by_owner(self, owner, opts):
        self.token_owner_calls += 1
        mint = Pubkey.from_string(TEST_TOKEN_MINT)
        mint_bytes = bytes(mint)
        owner_bytes = bytes([1] * 32)
        amount_bytes = (123_000_000).to_bytes(8, byteorder="little")
        account_data = mint_bytes + owner_bytes + amount_bytes + bytes([0] * 32)

        token_account = SimpleNamespace(
            pubkey=Pubkey.from_string("11111111111111111111111111111111"),
            account=SimpleNamespace(data=account_data),
        )
        return SimpleNamespace(value=[token_account])

    async def get_account_info(self, mint_pubkey):
        mint_data = bytearray(82)
        mint_data[44] = 6
        return SimpleNamespace(value=SimpleNamespace(data=bytes(mint_data)))

    async def get_signature_statuses(self, signatures):
        self.status_calls += 1
        if self.status_calls == 1:
            status = SimpleNamespace(confirmation_status="processed", err=None)
        else:
            status = SimpleNamespace(confirmation_status="confirmed", err=None)
        return SimpleNamespace(value=[status])

    async def close(self):
        return None


def build_wallet_client():
    wallet = object.__new__(WalletClient)
    wallet.rpc_url = "http://mock-rpc"
    wallet.client = FakeRpcClient()
    wallet.keypair = None
    wallet.public_key = Pubkey.from_string("11111111111111111111111111111111")
    wallet._balance_cache = {}
    wallet._token_accounts_cache = None
    wallet._cache_timestamp = 0
    wallet._token_metadata_cache = {}
    return wallet


@pytest.mark.asyncio
async def test_get_sol_balance_uses_cache(monkeypatch):
    wallet = build_wallet_client()
    first = await wallet.get_sol_balance(use_cache=True)
    second = await wallet.get_sol_balance(use_cache=True)
    assert first == 2.0
    assert second == 2.0
    assert wallet.client.balance_calls == 1


@pytest.mark.asyncio
async def test_get_token_accounts_with_mocked_rpc():
    wallet = build_wallet_client()
    mint = TEST_TOKEN_MINT
    wallet._token_metadata_cache[mint] = {
        "symbol": "mSOL",
        "name": "Mock SOL",
        "_cache_time": 9999999999,
    }

    accounts = await wallet.get_token_accounts(force_refresh=True)
    assert len(accounts) == 1
    assert accounts[0]["mint"] == mint
    assert accounts[0]["symbol"] == "mSOL"
    assert accounts[0]["decimals"] == 6
    assert accounts[0]["ui_amount"] == 123.0


@pytest.mark.asyncio
async def test_get_token_balance_for_sol_and_specific_token():
    wallet = build_wallet_client()
    mint = TEST_TOKEN_MINT
    wallet._token_metadata_cache[mint] = {
        "symbol": "mSOL",
        "name": "Mock SOL",
        "_cache_time": 9999999999,
    }

    sol_balance = await wallet.get_token_balance(SOL_MINT)
    token_balance = await wallet.get_token_balance(mint)
    assert sol_balance["ui_amount"] == 2.0
    assert token_balance["symbol"] == "mSOL"


@pytest.mark.asyncio
async def test_confirm_transaction_with_mocked_statuses(monkeypatch):
    wallet = build_wallet_client()

    async def fast_sleep(_):
        return None

    monkeypatch.setattr("core.wallet_client.asyncio.sleep", fast_sleep)
    confirmed = await wallet.confirm_transaction("sig-test", timeout=2)
    assert confirmed is True

