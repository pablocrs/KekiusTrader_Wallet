import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# Make engine import portable when uvloop is unavailable in test env.
if "uvloop" not in sys.modules:
    sys.modules["uvloop"] = SimpleNamespace(EventLoopPolicy=lambda: None)

import core.engine as engine_module


class DummyWalletClient:
    def __init__(self):
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True

    def get_public_key_str(self):
        return "FakeWalletPubKey"


class DummyTradingEngine:
    def __init__(self, wallet):
        self.wallet = wallet
        self.initialized = False
        self.closed = False

    async def initialize(self):
        self.initialized = True

    async def close(self):
        self.closed = True


class DummyWalletInfo:
    def __init__(self, wallet):
        self.wallet = wallet


class DummyApiServer:
    def __init__(self, trading_engine, wallet_info):
        self.app = object()


class DummyWebSocketServer:
    def __init__(self, app):
        self.app = app


class DummyUvicornConfig:
    def __init__(self, app, host, port, log_level):
        self.app = app
        self.host = host
        self.port = port
        self.log_level = log_level


class DummyUvicornServer:
    def __init__(self, config):
        self.config = config

    async def serve(self):
        return None


@pytest.mark.asyncio
async def test_engine_start_exits_without_private_key(monkeypatch):
    monkeypatch.setattr(engine_module.settings, "WALLET_PRIVATE_KEY", "", raising=False)
    engine = engine_module.WalletEngine()
    with pytest.raises(SystemExit):
        await engine.start()


@pytest.mark.asyncio
async def test_engine_start_and_stop_success(monkeypatch):
    monkeypatch.setattr(engine_module.settings, "WALLET_PRIVATE_KEY", "test-private-key", raising=False)
    monkeypatch.setattr(engine_module.settings, "API_PORT", 8003, raising=False)
    monkeypatch.setattr(engine_module, "WalletClient", DummyWalletClient)
    monkeypatch.setattr(engine_module, "TradingEngine", DummyTradingEngine)
    monkeypatch.setattr(engine_module, "WalletInfo", DummyWalletInfo)
    monkeypatch.setattr(engine_module, "ApiServer", DummyApiServer)
    monkeypatch.setattr(engine_module, "WebSocketServer", DummyWebSocketServer)
    monkeypatch.setattr(engine_module.uvicorn, "Config", DummyUvicornConfig)
    monkeypatch.setattr(engine_module.uvicorn, "Server", DummyUvicornServer)

    engine = engine_module.WalletEngine()
    await engine.start()

    assert engine.running is True
    assert engine.wallet is not None
    assert engine.trading_engine is not None

    await engine.stop()
    assert engine.running is False
    assert engine.wallet.closed is True
    assert engine.trading_engine.closed is True
