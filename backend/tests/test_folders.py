"""Media folder as a runtime setting: effective dir, validation, confirm rule, restart, routes."""

import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, settings
from app.db import SessionLocal
from app.main import app
from app.models import AppState, MediaItem
from app.services import folders, scanner

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


@pytest.fixture
def calls(monkeypatch):
    rec = []
    monkeypatch.setattr(scanner, "stop_background", lambda *a, **k: rec.append(("stop",)))
    monkeypatch.setattr(scanner, "start_background", lambda d: rec.append(("start", d)))
    return rec


def add(db, name, missing=False):
    db.add(MediaItem(path=name, sha256=hashlib.sha256(name.encode()).hexdigest(), kind="image", missing=missing))
    db.commit()


def new_folder(tmp_path, name="other"):
    d = tmp_path / name
    d.mkdir()
    return d


def test_effective_dir_defaults_to_settings(media_dir, monkeypatch, tmp_path):
    assert folders.current_media_dir() == media_dir
    other = new_folder(tmp_path, "later")
    monkeypatch.setattr(settings, "media_dir", other)  # read live, never cached
    assert folders.current_media_dir() == other


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A fake app layout (real directories) so every rejection is for the reserved-folder rule."""
    root = tmp_path / "app"
    for sub in ("data", "data/inner", "profiles", "profiles/test/media", "media", "other", "pdata"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(folders, "ROOT", root)
    monkeypatch.setattr(settings, "data_dir", root / "pdata")
    monkeypatch.setattr(settings, "profiles_dir", root / "profiles")
    monkeypatch.setattr(settings, "media_dir", root / "media")
    return root


def test_set_folder_validates_path(db, layout, calls, tmp_path):
    f = tmp_path / "a-file.txt"
    f.write_text("x")
    cases = {
        "relative/dir": "full path",
        "": "Choose a folder",
        str(tmp_path / "missing"): "doesn't exist",
        str(f): "doesn't exist",
        str(layout / "data"): "own data folder",  # the repo's data/ itself
        str(layout / "data" / "inner"): "own data folder",
        str(settings.data_dir): "own data folder",  # the profile's data dir
        str(layout): "own data folder",  # contains data/
        str(layout / "profiles"): "profiles folder",
        str(layout / "profiles" / "test" / "media"): "profiles folder",
    }
    for p, msg in cases.items():
        with pytest.raises(ValueError, match=msg):
            folders.set_media_dir(db, p, confirm=True)
    assert calls == []
    assert db.get(AppState, "media_dir") is None
    assert folders.current_media_dir() == layout / "media"
    assert folders.set_media_dir(db, str(layout / "other"), confirm=True) == (layout / "other").resolve()


def test_change_folder_requires_confirm_when_collection_exists(db, media_dir, calls, tmp_path):
    other = new_folder(tmp_path)
    add(db, "gone.png", missing=True)
    folders.set_media_dir(db, str(other), confirm=False)  # only missing media: no confirm needed
    folders.reset_media_dir(db, confirm=False)
    calls.clear()
    add(db, "a.png")
    add(db, "b.png")
    with pytest.raises(folders.FolderChangeNeedsConfirm) as e:
        folders.set_media_dir(db, str(other), confirm=False)
    assert e.value.count == 2
    assert calls == [] and folders.current_media_dir() == media_dir
    assert db.get(AppState, "media_dir") is None
    assert folders.set_media_dir(db, str(other), confirm=True) == other.resolve()


def test_change_folder_restarts_scanner(db, media_dir, calls, tmp_path):
    other = new_folder(tmp_path)
    folders.set_media_dir(db, str(other), confirm=True)
    assert calls == [("stop",), ("start", other.resolve())]
    assert folders.current_media_dir() == other.resolve()
    assert db.get(AppState, "media_dir").value == str(other.resolve())
    calls.clear()
    folders.reset_media_dir(db, confirm=True)
    assert calls == [("stop",), ("start", media_dir)]
    assert folders.current_media_dir() == media_dir
    assert db.get(AppState, "media_dir") is None


def test_failed_restart_rolls_back(db, media_dir, monkeypatch, tmp_path):
    other = new_folder(tmp_path)
    started = []

    def start(d):
        started.append(d)
        if d == other.resolve():
            return False  # the watcher could not start

    monkeypatch.setattr(scanner, "stop_background", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "start_background", start)
    with pytest.raises(ValueError):
        folders.set_media_dir(db, str(other), confirm=True)
    assert folders.current_media_dir() == media_dir
    assert db.get(AppState, "media_dir") is None
    assert started == [other.resolve(), media_dir]  # old folder watched again


def test_start_background_reports_watcher_failure(media_dir, monkeypatch):
    def boom(self):
        raise OSError("no watcher")

    monkeypatch.setattr(scanner.settings, "watch_media", True)  # the watcher is opt-in since v0.3.0
    monkeypatch.setattr(scanner.MediaWatcher, "start", boom)
    monkeypatch.setattr(scanner, "full_scan", lambda *a, **k: {})
    try:
        assert scanner.start_background(media_dir) is False
    finally:
        scanner.stop_background()


def test_failed_restart_after_vanished_startup_keeps_saved_row(db, media_dir, monkeypatch, tmp_path):
    other = new_folder(tmp_path)
    gone = tmp_path / "unplugged"
    db.add(AppState(key="media_dir", value=str(gone)))
    db.commit()
    folders.load_override(db)
    assert folders.current_media_dir() == media_dir
    monkeypatch.setattr(scanner, "stop_background", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "start_background", lambda d: d != other.resolve())
    with pytest.raises(ValueError):
        folders.set_media_dir(db, str(other), confirm=True)
    db.expire_all()
    assert db.get(AppState, "media_dir").value == str(gone)  # the saved folder is not lost
    assert folders.current_media_dir() == media_dir


def test_saved_unavailable_is_reported(db, media_dir):
    gone = media_dir.parent / "unplugged"
    db.add(AppState(key="media_dir", value=str(gone)))
    db.commit()
    folders.load_override(db)
    r = client.get("/api/settings/media-folder").json()
    assert r["saved_unavailable"] == str(gone) and r["overridden"] is False
    assert db.get(AppState, "media_dir").value == str(gone)


def test_concurrent_switches_stay_consistent(db, media_dir, monkeypatch, tmp_path):
    import threading
    import time

    dirs = [new_folder(tmp_path, f"d{i}") for i in range(4)]
    monkeypatch.setattr(scanner, "stop_background", lambda *a, **k: time.sleep(0.02))
    monkeypatch.setattr(scanner, "start_background", lambda d: time.sleep(0.02) or True)
    errors = []

    def go(d):
        try:
            with SessionLocal() as s:
                folders.set_media_dir(s, str(d), confirm=True)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=go, args=(d,)) for d in dirs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    db.expire_all()
    assert db.get(AppState, "media_dir").value == str(folders.current_media_dir())


def test_stale_scan_does_not_mark_missing_after_switch(db, media_dir, calls, monkeypatch, tmp_path):
    import threading
    import time

    from PIL import Image

    old = media_dir
    for i in range(2):
        Image.new("RGB", (20, 20), (i * 90, 5, 5)).save(old / f"i{i}.png")
        t = time.time() - 60
        os.utime(old / f"i{i}.png", (t, t))
    add(db, "elsewhere.png")  # a present card not in the old folder: a finished old scan would hide it
    started, release = threading.Event(), threading.Event()
    real = scanner.ingest_path

    def blocking(*a, **k):
        started.set()
        release.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(scanner, "ingest_path", blocking)
    result = {}
    th = threading.Thread(target=lambda: result.update(scanner.full_scan(old, expect_current=True)))
    th.start()
    assert started.wait(10)
    other = new_folder(tmp_path)
    folders.set_media_dir(db, str(other), confirm=True)  # switch while the old scan is mid-file
    release.set()
    th.join(10)
    assert not th.is_alive()
    db.expire_all()
    assert db.query(MediaItem).filter_by(path="elsewhere.png").one().missing is False
    assert result["missing"] == 0
    assert db.query(MediaItem).filter(MediaItem.path.like("i%.png")).count() <= 1  # no further ingest


def test_start_background_uses_fresh_stop_event(media_dir, monkeypatch):
    import threading

    gate = threading.Event()
    seen = []

    def slow_scan(d, *, stop=None, **kw):
        seen.append(stop)
        gate.wait(10)
        return {}

    monkeypatch.setattr(scanner, "full_scan", slow_scan)
    monkeypatch.setattr(scanner.MediaWatcher, "start", lambda self: None)
    scanner.start_background(media_dir)
    scanner.stop_background(timeout=0.2)  # old scan still running, abandoned
    scanner.start_background(media_dir)
    try:
        assert seen[0] is not seen[1]
        assert seen[0].is_set() and not seen[1].is_set()  # the old run stays stopped
    finally:
        gate.set()
        scanner.stop_background()


def test_override_survives_restart(db, media_dir, calls, tmp_path):
    other = new_folder(tmp_path)
    folders.set_media_dir(db, str(other), confirm=True)
    folders._override = None  # simulate a fresh process
    assert folders.current_media_dir() == media_dir
    folders.load_override(db)
    assert folders.current_media_dir() == other.resolve()


def test_override_ignored_if_folder_vanished(db, media_dir):
    db.add(AppState(key="media_dir", value=str(media_dir.parent / "nope")))
    db.commit()
    folders.load_override(db)
    assert folders.current_media_dir() == media_dir


def test_media_routes_use_effective_dir(db, media_dir, calls, tmp_path):
    other = new_folder(tmp_path)
    (other / "p.txt").write_text("x")
    add(db, "p.txt")
    m = db.query(MediaItem).one()
    assert client.get(f"/api/media/{m.id}/file").status_code == 404  # not in the default folder
    folders.set_media_dir(db, str(other), confirm=True)
    r = client.get(f"/api/media/{m.id}/file")
    assert r.status_code == 200 and r.content == b"x"
    assert client.get("/api/health").json()["media_dir"] == str(other.resolve())


def test_get_route(db, media_dir):
    add(db, "a.png")
    r = client.get("/api/settings/media-folder")
    assert r.status_code == 200
    assert r.json() == {
        "path": str(media_dir),
        "default": str(media_dir),
        "overridden": False,
        "exists": True,
        "media_count": 1,
        "saved_unavailable": None,
    }


def test_put_route_status_codes(db, media_dir, calls, tmp_path):
    other = new_folder(tmp_path)
    add(db, "a.png")
    r = client.put("/api/settings/media-folder", json={"path": str(other)})
    assert r.status_code == 409 and r.json()["detail"] == {"needs_confirm": True, "media_count": 1}
    r = client.put("/api/settings/media-folder", json={"path": "nope"})
    assert r.status_code == 422 and r.json()["detail"]["message"]
    r = client.put("/api/settings/media-folder", json={"path": str(other), "confirm": True})
    assert r.status_code == 200
    assert r.json()["path"] == str(other.resolve()) and r.json()["overridden"] is True
    # choosing the folder already in use is a no-op, not a confirm prompt
    assert client.put("/api/settings/media-folder", json={"path": str(other)}).status_code == 200


def test_delete_route(db, media_dir, calls, tmp_path):
    other = new_folder(tmp_path)
    folders.set_media_dir(db, str(other), confirm=True)
    add(db, "a.png")
    assert client.delete("/api/settings/media-folder").status_code == 409
    r = client.delete("/api/settings/media-folder?confirm=true")
    assert r.status_code == 200 and r.json()["overridden"] is False and r.json()["path"] == str(media_dir)


def test_open_route(media_dir, monkeypatch):
    opened = []
    monkeypatch.setattr(folders, "open_media_dir", lambda: opened.append(1))
    assert client.post("/api/settings/media-folder/open").status_code == 204
    assert opened == [1]

    def boom():
        raise RuntimeError("no file manager")

    monkeypatch.setattr(folders, "open_media_dir", boom)
    r = client.post("/api/settings/media-folder/open")
    assert r.status_code == 500 and r.json()["detail"]["message"] == "no file manager"


def test_open_media_dir_per_os(media_dir, monkeypatch):
    import os
    import subprocess
    import sys

    from app.services import desktop

    ran = []
    monkeypatch.setattr(desktop, "_explorer_window_ids", set)
    monkeypatch.setattr(desktop, "_bring_explorer_to_front", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append(cmd))
    monkeypatch.setattr(os, "startfile", lambda p: ran.append(("startfile", p)), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    folders.open_media_dir()
    monkeypatch.setattr(sys, "platform", "darwin")
    folders.open_media_dir()
    monkeypatch.setattr(sys, "platform", "linux")
    folders.open_media_dir()
    assert ran == [("startfile", str(media_dir)), ["open", str(media_dir)], ["xdg-open", str(media_dir)]]

    def fail(cmd, **k):
        raise OSError("nope")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(RuntimeError):
        folders.open_media_dir()


def test_open_media_dir_requires_existing_folder(media_dir):
    import shutil

    shutil.rmtree(media_dir)
    with pytest.raises(RuntimeError, match="isn't available"):
        folders.open_media_dir()
