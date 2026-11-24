"""CSRF and DNS-rebinding protection: custom header on mutating /api requests, Host allow-list."""

from fastapi.testclient import TestClient

from app.main import app

HDR = {"X-MCO-Client": "1"}


def test_mutating_request_without_client_header_is_forbidden():
    with TestClient(app) as c:
        for url in ("/api/economy/checkin", "/api/media/scan", "/api/packs/starter/open"):
            r = c.post(url)
            assert r.status_code == 403, url
        assert c.put("/api/tutorials/progress/welcome", json={"status": "completed"}).status_code == 403
        assert c.delete("/api/tutorials/progress").status_code == 403
        # nothing was paid out
        assert [t["reason"] for t in c.get("/api/economy/transactions").json()] == ["starting_grant"]


def test_mutating_request_with_client_header_is_allowed():
    with TestClient(app) as c:
        assert c.post("/api/economy/checkin", headers=HDR).status_code == 200
        assert c.put("/api/tutorials/progress/welcome", json={"status": "completed"}, headers=HDR).status_code == 200


def test_get_needs_no_header():
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/media").status_code == 200


def test_bad_host_is_rejected():
    with TestClient(app) as c:
        assert c.get("/api/media", headers={"Host": "evil.example"}).status_code == 400
        assert c.get("/api/health", headers={"Host": "localhost:5173"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_presence_websocket_still_works():
    with TestClient(app) as c:
        with c.websocket_connect("/api/ws/presence") as ws:
            ws.send_text("ping")


def test_shutdown_keeps_its_own_header_and_needs_no_client_header():
    calls = []
    with TestClient(app) as c:
        app.state.shutdown_callback = calls.append
        try:
            assert c.post("/api/shutdown").status_code == 400  # reaches the handler, which wants X-MCO-Action
            assert c.post("/api/shutdown", headers={"X-MCO-Action": "shutdown"}).status_code == 200
        finally:
            app.state.shutdown_callback = None
    assert calls == ["stop requested from the app"]
