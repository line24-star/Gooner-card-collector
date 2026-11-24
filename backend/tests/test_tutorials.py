from fastapi.testclient import TestClient

from app.main import app


def test_progress_roundtrip():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        assert c.delete("/api/tutorials/progress").status_code == 204
        assert c.get("/api/tutorials/progress").json() == []

        r = c.put("/api/tutorials/progress/welcome", json={"status": "skipped"})
        assert r.status_code == 200 and r.json()["status"] == "skipped"

        # Updating the same tutorial replaces its status instead of duplicating it.
        c.put("/api/tutorials/progress/welcome", json={"status": "completed"})
        rows = c.get("/api/tutorials/progress").json()
        assert [(x["tutorial_id"], x["status"]) for x in rows] == [("welcome", "completed")]

        assert c.delete("/api/tutorials/progress").status_code == 204
        assert c.get("/api/tutorials/progress").json() == []


def test_rejects_bad_ids_and_statuses():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        assert c.put("/api/tutorials/progress/Bad_ID!", json={}).status_code == 422
        assert c.put("/api/tutorials/progress/ok", json={"status": "nope"}).status_code == 422
