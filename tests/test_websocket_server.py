import sys
from pathlib import Path
import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.websocket_server import WebSocketServer


def test_websocket_connect_and_welcome_message():
    app = FastAPI()
    ws_server = WebSocketServer(app)
    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        welcome = websocket.receive_json()
        assert welcome["type"] == "connected"
        assert "features" in welcome
        assert len(ws_server.active_connections) == 1

    assert len(ws_server.active_connections) == 0


def test_websocket_broadcast_integration():
    app = FastAPI()
    ws_server = WebSocketServer(app)
    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()  # welcome
        asyncio.run(
            ws_server.broadcast_transaction(
                tx_type="BUY",
                signature="sig-123",
                status="confirmed",
                details={"mint": "SomeMint"},
            )
        )
        payload = json.loads(websocket.receive_text())
        assert payload["type"] == "transaction"
        assert payload["tx_type"] == "BUY"
        assert payload["signature"] == "sig-123"


@pytest.mark.asyncio
async def test_websocket_disconnect_removes_connection():
    app = FastAPI()
    ws_server = WebSocketServer(app)

    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.sent = []

        async def accept(self):
            self.accepted = True

        async def send_json(self, payload):
            self.sent.append(payload)

    fake = FakeWebSocket()
    await ws_server.connect(fake)
    assert len(ws_server.active_connections) == 1
    await ws_server.disconnect(fake)
    assert len(ws_server.active_connections) == 0
