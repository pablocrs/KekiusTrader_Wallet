import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.api_server import ApiServer


class FakeWallet:
    def get_public_key_str(self):
        return "FakeWalletPubKey"


class FakeWalletInfo:
    def __init__(self):
        self.wallet = FakeWallet()

    async def get_full_wallet_info(self):
        return {"sol": 10.0, "tokens": []}

    async def get_balance_summary(self):
        return {"sol_balance": 10.0}

    async def get_token_balance_info(self, mint: str):
        return {"mint": mint, "ui_amount": 1.23}


class FakeTradingEngine:
    def __init__(self):
        self.buy_result = {"success": True, "signature": "sig-buy"}
        self.sell_result = {"success": True, "signature": "sig-sell"}
        self.sell_all_result = {"success": True, "signature": "sig-sell-all"}
        self.convert_result = {"success": True, "signature": "sig-convert"}
        self.convert_sol_result = {"success": True, "signature": "sig-convert-sol"}
        self.last_buy_kwargs = {}

    async def buy(self, **kwargs):
        self.last_buy_kwargs = dict(kwargs)
        return self.buy_result

    async def sell(self, **kwargs):
        return self.sell_result

    async def sell_all(self, **kwargs):
        return self.sell_all_result

    async def convert_profit_to_usdc(self):
        return self.convert_result

    async def convert_sol_to_usdc(self, **kwargs):
        return self.convert_sol_result


@pytest.fixture
def api_client():
    trading_engine = FakeTradingEngine()
    wallet_info = FakeWalletInfo()
    server = ApiServer(trading_engine, wallet_info)
    return TestClient(server.app), trading_engine


def test_health_endpoint(api_client):
    client, _ = api_client
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "wallet" in data
    assert "features" in data


def test_buy_success(api_client):
    client, _ = api_client
    payload = {"mint": "SomeMint", "amount_sol": 0.1}
    response = client.post("/buy", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["signature"] == "sig-buy"


def test_buy_failure_returns_500(api_client):
    client, engine = api_client
    engine.buy_result = {"success": False, "error": "buy failed for testing"}
    payload = {"mint": "SomeMint", "amount_sol": 0.1}
    response = client.post("/buy", json=payload)
    assert response.status_code == 500
    assert "buy failed for testing" in response.json()["detail"]


def test_buy_validation_failure_returns_400(api_client):
    client, engine = api_client
    engine.buy_result = {"success": False, "error": "Invalid mint address"}
    payload = {"mint": "SomeMint", "amount_sol": 0.1}
    response = client.post("/buy", json=payload)
    assert response.status_code == 400
    assert "Invalid mint address" in response.json()["detail"]


def test_buy_with_usdc_amount_routes_denom(api_client):
    client, engine = api_client
    payload = {"mint": "SomeMint", "amount": 25.5, "spend_denom": "USDC"}
    response = client.post("/buy", json=payload)
    assert response.status_code == 200
    assert engine.last_buy_kwargs["amount"] == 25.5
    assert engine.last_buy_kwargs["spend_denom"] == "USDC"


def test_buy_with_amount_only_defaults_to_sol(api_client):
    client, engine = api_client
    payload = {"mint": "SomeMint", "amount": 0.1}
    response = client.post("/buy", json=payload)
    assert response.status_code == 200
    assert engine.last_buy_kwargs["amount"] == 0.1
    assert engine.last_buy_kwargs["spend_denom"] == "SOL"


def test_buy_missing_amount_returns_400(api_client):
    client, _ = api_client
    payload = {"mint": "SomeMint", "spend_denom": "USDC"}
    response = client.post("/buy", json=payload)
    assert response.status_code == 400


def test_sell_failure_returns_500(api_client):
    client, engine = api_client
    engine.sell_result = {"success": False, "error": "sell failed for testing"}
    payload = {"mint": "SomeMint", "amount": 1000}
    response = client.post("/sell", json=payload)
    assert response.status_code == 500
    assert "sell failed for testing" in response.json()["detail"]


def test_sell_all_none_returns_400(api_client):
    client, engine = api_client
    engine.sell_all_result = None
    payload = {"mint": "SomeMint"}
    response = client.post("/sell-all", json=payload)
    assert response.status_code == 400


def test_sell_all_failure_returns_500(api_client):
    client, engine = api_client
    engine.sell_all_result = {"success": False, "error": "sell-all failed for testing"}
    payload = {"mint": "SomeMint"}
    response = client.post("/sell-all", json=payload)
    assert response.status_code == 500
    assert "sell-all failed for testing" in response.json()["detail"]


def test_profit_convert_no_result_returns_success_false(api_client):
    client, engine = api_client
    engine.convert_result = None
    response = client.post("/profit/convert")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False


def test_convert_sol_to_usdc_success(api_client):
    client, engine = api_client
    engine.convert_sol_result = {"success": True, "signature": "sig-convert-sol"}
    response = client.post("/convert/sol-to-usdc", json={"amount_sol": 0.2})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["signature"] == "sig-convert-sol"


def test_convert_sol_to_usdc_failure_returns_400(api_client):
    client, engine = api_client
    engine.convert_sol_result = {"success": False, "error": "insufficient SOL after reserve"}
    response = client.post("/convert/sol-to-usdc", json={"amount_sol": 100})
    assert response.status_code == 400
    assert "insufficient SOL after reserve" in response.json()["detail"]
