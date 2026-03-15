import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.dex.jupiter_client import JupiterClient


class FakeResponse:
    def __init__(self, status=200, payload=None, text_payload="error", headers=None):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text_payload = text_payload
        self.headers = headers or {}

    async def json(self):
        return self._payload

    async def text(self):
        return self._text_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, quote_response=None, swap_response=None, quote_responses=None, swap_responses=None):
        self.quote_response = quote_response or FakeResponse(
            status=200,
            payload={"outAmount": "100", "priceImpactPct": 0.2},
        )
        self.swap_response = swap_response or FakeResponse(
            status=200,
            payload={"swapTransaction": "base64tx"},
        )
        self.quote_responses = list(quote_responses or [])
        self.swap_responses = list(swap_responses or [])
        self.get_calls = 0
        self.post_calls = 0
        self.closed = False

    def get(self, url, params=None):
        self.get_calls += 1
        if self.quote_responses:
            return self.quote_responses.pop(0)
        return self.quote_response

    def post(self, url, json=None):
        self.post_calls += 1
        if self.swap_responses:
            return self.swap_responses.pop(0)
        return self.swap_response

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_execute_swap_success(monkeypatch):
    client = JupiterClient()
    client.session = FakeSession()

    result = await client.execute_swap(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="SomeMint",
        amount=1000,
        user_public_key="FakeWallet",
    )

    assert result is not None
    assert result["transaction"] == "base64tx"
    assert result["output_amount"] == 100


@pytest.mark.asyncio
async def test_get_quote_error_response(monkeypatch):
    client = JupiterClient()
    client.session = FakeSession(quote_response=FakeResponse(status=500, text_payload="boom"))

    result = await client.get_quote(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="SomeMint",
        amount=1000,
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_quote_retries_on_429_then_succeeds():
    client = JupiterClient()
    client.session = FakeSession(
        quote_responses=[
            FakeResponse(status=429, text_payload="rate limit", headers={"Retry-After": "0"}),
            FakeResponse(status=200, payload={"outAmount": "77", "priceImpactPct": 0.1}),
        ]
    )

    result = await client.get_quote(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="SomeMint",
        amount=1000,
    )

    assert result is not None
    assert result["outAmount"] == "77"
    assert client.session.get_calls == 2


@pytest.mark.asyncio
async def test_close_resets_session():
    client = JupiterClient()
    fake_session = FakeSession()
    client.session = fake_session
    await client.close()
    assert fake_session.closed is True
    assert client.session is None
