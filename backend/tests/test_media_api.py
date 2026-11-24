"""Media API: list/detail/thumb/file/scan routes and the CardOut payload."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.db import SessionLocal
from app.game.config import get_config
from app.main import app
from app.models import CollectionEntry, MediaItem, Tag
from app.services import cards, rewards
from app.services.wallet import local_user

cfg = get_config()
client = TestClient(app, headers={"X-MCO-Client": "1"})


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(settings, "media_dir", media)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    return media


def add(db, name, kind="image", **kw):
    m = MediaItem(path=name, sha256=hashlib.sha256(name.encode()).hexdigest(), kind=kind, **kw)
    db.add(m)
    db.commit()
    return m


def own(db, media, treatment, copies=1):
    u = local_user(db)
    db.add(CollectionEntry(user_id=u.id, media_id=media.id, treatment=treatment, copies=copies))
    db.commit()


def ids(resp):
    return {i["id"] for i in resp.json()["items"]}


def test_list_media_filters(db):
    a = add(db, "Beach.jpg")
    b = add(db, "forest.png")
    c = add(db, "sub/city.jpg")
    parent = Tag(name="places")
    db.add(parent)
    db.commit()
    child = Tag(name="urban", parent_id=parent.id)
    other = Tag(name="misc")
    db.add_all([child, other])
    db.commit()
    a.tags.append(parent)
    c.tags.append(child)
    b.tags.append(other)
    db.commit()
    own(db, a, "holo")
    own(db, b, "normal")

    r = client.get("/api/media")
    assert r.json()["total"] == 3 and ids(r) == {a.id, b.id, c.id}
    assert ids(client.get("/api/media?filter=untagged")) == set()
    c.tags.clear()
    db.commit()
    assert ids(client.get("/api/media?filter=untagged")) == {c.id}
    assert ids(client.get("/api/media?filter=pulled")) == {a.id, b.id}
    assert ids(client.get("/api/media?filter=holo")) == {a.id}
    c.tags.append(child)
    db.commit()
    assert ids(client.get(f"/api/media?tag={parent.id}")) == {a.id, c.id}  # includes child tag
    assert ids(client.get(f"/api/media?tag={child.id}")) == {c.id}
    assert ids(client.get("/api/media?q=BEACH")) == {a.id}
    assert ids(client.get("/api/media?q=city")) == {c.id}
    names = [i["title"] for i in client.get("/api/media?sort=name").json()["items"]]
    assert names == sorted(names, key=str.lower)
    page = client.get("/api/media?limit=2&offset=2").json()
    assert page["total"] == 3 and len(page["items"]) == 1


def test_list_media_limit_capped(db):
    db.add_all([
        MediaItem(path=f"m{i}.jpg", sha256=hashlib.sha256(str(i).encode()).hexdigest(), kind="image")
        for i in range(520)
    ])
    db.commit()
    r = client.get("/api/media?limit=10000")
    assert r.json()["total"] == 520
    assert len(r.json()["items"]) == 500


def test_list_excludes_missing(db):
    a = add(db, "a.jpg")
    b = add(db, "b.jpg", missing=True)
    assert ids(client.get("/api/media")) == {a.id}
    assert client.get("/api/media").json()["total"] == 1
    assert client.get(f"/api/media/{b.id}").status_code == 404


@pytest.mark.upgrades_on
def test_card_out_fields(db):
    m = add(db, "holo pic.jpg", width=10, height=20)
    v = add(db, "clip.mp4", kind="video")
    u = local_user(db)
    own(db, m, "normal")
    own(db, m, "holo", copies=2)
    c = client.get(f"/api/media/{m.id}").json()
    # v0.9.0: the file name no longer makes the name; it stays on the payload
    assert c["title"] == f"Card {c['code']}" and c["file_name"] == "holo pic" and c["title_custom"] is False
    assert c["kind"] == "image"
    assert c["thumb_url"] == f"/api/media/{m.id}/thumb"
    assert c["file_url"] == f"/api/media/{m.id}/file"
    assert c["treatment"] == "holo"
    assert c["owned"] == {"normal": True, "holo": True}
    assert c["copies"] == 3
    assert c["width"] == 10 and c["height"] == 20 and c["added_at"]
    assert c["upgrade_cost"] == cfg.upgrades.cost["holo"]
    assert c["sell_prices"]["holo"] > 0 and set(c["sell_prices"]) == {"normal", "holo"}
    assert c["review_available"] is False  # untagged
    assert c["review_reward"] is None
    assert client.get(f"/api/media/{v.id}").json()["thumb_url"] is None
    assert client.get(f"/api/media/{v.id}").json()["treatment"] == "normal"

    t = Tag(name="x")
    m.tags.append(t)
    db.commit()
    c = client.get(f"/api/media/{m.id}").json()
    assert c["review_available"] is True  # no tag payout recorded: counts as tagged long ago
    assert c["review_reward"] == round(cfg.tagging.upgrade_review * cfg.treatments["holo"].multiplier)
    assert c["tags"] == [{"id": t.id, "name": "x", "parent_id": None}]
    assert rewards.reward_review(db, u, m.id, "holo") > 0
    db.commit()
    assert client.get(f"/api/media/{m.id}").json()["review_available"] is False
    assert client.get(f"/api/media/{m.id}").json()["review_reward"] is None
    # batch helper agrees with the single one
    assert cards.card_out_many(db, u, [m])[0] == cards.card_out(db, u, m)


def test_thumb_and_file_routes(db, media_dir):
    p = media_dir / "pic.png"
    Image.new("RGB", (800, 400), "red").save(p)
    m = add(db, "pic.png")
    r = client.get(f"/api/media/{m.id}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    r = client.get(f"/api/media/{m.id}/file")
    assert r.status_code == 200 and r.content == p.read_bytes()
    v = add(db, "clip.mp4", kind="video")
    (media_dir / "clip.mp4").write_bytes(b"x")
    assert client.get(f"/api/media/{v.id}/thumb").status_code == 404
    assert client.get("/api/media/9999/thumb").status_code == 404
    assert client.get("/api/media/9999/file").status_code == 404


def test_file_route_rejects_escape(db, media_dir):
    (media_dir.parent / "outside.png").write_bytes(b"secret")
    m = add(db, "../outside.png")
    assert client.get(f"/api/media/{m.id}/file").status_code == 404
    assert client.get(f"/api/media/{m.id}/thumb").status_code == 404


def test_scan_routes(media_dir, monkeypatch):
    from app.routers import media as media_router
    from app.services import scanner

    calls = []
    monkeypatch.setattr(scanner, "full_scan", lambda d, **kw: calls.append(d) or {})
    r = client.get("/api/media/scan")
    assert r.status_code == 200 and r.json()["state"] in ("idle", "scanning")
    assert {"done", "total", "last_report"} <= set(r.json())
    r = client.post("/api/media/scan")
    assert r.status_code == 202 and r.json() == {"started": True}
    media_router._scan_thread.join(timeout=5)
    assert not media_router._scan_thread.is_alive()
    assert calls == [media_dir]
    monkeypatch.setattr(scanner, "get_status", lambda: scanner.ScanStatus("scanning", 1, 2, None))
    r = client.post("/api/media/scan")
    assert r.status_code == 202 and r.json() == {"started": False}


def test_scan_failure_is_contained_and_guard_frees(media_dir, monkeypatch):
    from app.routers import media as media_router
    from app.services import scanner

    def boom(d, **kw):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(scanner, "full_scan", boom)
    assert client.post("/api/media/scan").json() == {"started": True}
    media_router._scan_thread.join(timeout=5)
    assert not media_router._scan_thread.is_alive()
    calls = []
    monkeypatch.setattr(scanner, "full_scan", lambda d, **kw: calls.append(d) or {})
    assert client.post("/api/media/scan").json() == {"started": True}
    media_router._scan_thread.join(timeout=5)
    assert calls == [media_dir]


def test_unknown_filter_is_422():
    assert client.get("/api/media?filter=bogus").status_code == 422
    assert client.get("/api/media?filter=holo").status_code == 200
