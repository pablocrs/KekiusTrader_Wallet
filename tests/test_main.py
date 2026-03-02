import asyncio
import importlib
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import main as main_module


class FakeLoop:
    def __init__(self, raise_on_signal=False):
        self.handlers = []
        self.raise_on_signal = raise_on_signal

    def add_signal_handler(self, sig, callback):
        if self.raise_on_signal:
            raise NotImplementedError("signals unavailable")
        self.handlers.append((sig, callback))


class FakeEngine:
    def __init__(self, start_error=None):
        self.start_error = start_error
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True
        if self.start_error is not None:
            raise self.start_error

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_main_starts_and_stops_engine(monkeypatch):
    fake_engine = FakeEngine()
    fake_loop = FakeLoop()

    monkeypatch.setattr(main_module, "WalletEngine", lambda: fake_engine)
    monkeypatch.setattr(main_module.asyncio, "get_running_loop", lambda: fake_loop)

    await main_module.main()
    assert fake_engine.started is True
    assert fake_engine.stopped is True
    assert len(fake_loop.handlers) == 2


@pytest.mark.asyncio
async def test_main_handles_fatal_start_error(monkeypatch):
    fake_engine = FakeEngine(start_error=RuntimeError("fatal"))
    fake_loop = FakeLoop(raise_on_signal=True)

    monkeypatch.setattr(main_module, "WalletEngine", lambda: fake_engine)
    monkeypatch.setattr(main_module.asyncio, "get_running_loop", lambda: fake_loop)

    with pytest.raises(SystemExit) as exc:
        await main_module.main()
    assert exc.value.code == 1
    assert fake_engine.stopped is True


def test_module_entrypoint_uses_asyncio_run(monkeypatch):
    calls = {"ran": False}

    def fake_run(coro):
        calls["ran"] = True
        coro.close()

    monkeypatch.setattr(asyncio, "run", fake_run)
    runpy.run_module("main", run_name="__main__")
    assert calls["ran"] is True

