import asyncio

from fastapi.testclient import TestClient

from app.lifecycle import PresenceMonitor
from app.main import app


def run(coro):
    return asyncio.run(coro)


def test_shuts_down_after_last_client_leaves():
    calls = []

    async def scenario():
        m = PresenceMonitor(calls.append, grace_seconds=0.05)
        m.connect()
        m.disconnect()
        await asyncio.sleep(0.1)

    run(scenario())
    assert calls == ["all browser tabs disconnected"]


def test_reconnect_within_grace_cancels_shutdown():
    calls = []

    async def scenario():
        m = PresenceMonitor(calls.append, grace_seconds=0.05)
        m.connect()
        m.disconnect()
        m.connect()  # e.g. page reload
        await asyncio.sleep(0.1)

    run(scenario())
    assert calls == []


def test_stays_up_while_another_tab_is_open():
    calls = []

    async def scenario():
        m = PresenceMonitor(calls.append, grace_seconds=0.05)
        m.connect()
        m.connect()
        m.disconnect()
        await asyncio.sleep(0.1)

    run(scenario())
    assert calls == []


def test_disabled_never_shuts_down():
    calls = []

    async def scenario():
        m = PresenceMonitor(calls.append, grace_seconds=0.05, enabled=False)
        m.connect()
        m.disconnect()
        await asyncio.sleep(0.1)

    run(scenario())
    assert calls == []


def test_shutdown_endpoint_requires_header():
    calls = []
    with TestClient(app, headers={"X-MCO-Client": "1"}) as client:
        app.state.shutdown_callback = calls.append
        assert client.post("/api/shutdown").status_code == 400
        r = client.post("/api/shutdown", headers={"X-MCO-Action": "shutdown"})
    app.state.shutdown_callback = None
    assert r.status_code == 200
    assert calls == ["stop requested from the app"]


def test_presence_websocket_tracks_clients():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as client:
        with client.websocket_connect("/api/ws/presence") as ws:
            ws.send_text("ping")
            assert app.state.presence.clients == 1
