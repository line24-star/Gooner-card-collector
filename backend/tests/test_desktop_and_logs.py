"""The system folder dialog, the log files and the browser's error reports."""

import logging
import os
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import logs
from app.config import settings
from app.main import app
from app.services import desktop, folders


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


def _dialog(monkeypatch, *, out=b"", code=0, err=b""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_pick_folder_returns_the_chosen_folder(tmp_path, monkeypatch):
    calls = _dialog(monkeypatch, out=str(tmp_path).encode())
    assert desktop.pick_folder(tmp_path) == tmp_path
    assert calls[0][-2] == str(tmp_path)  # the dialog starts in the current folder


def test_pick_folder_cancelled_is_none(tmp_path, monkeypatch):
    _dialog(monkeypatch, out=b"")
    assert desktop.pick_folder(tmp_path) is None


def test_pick_folder_reports_a_dialog_that_cannot_open(tmp_path, monkeypatch):
    _dialog(monkeypatch, code=1, err=b"no display")
    with pytest.raises(RuntimeError):
        desktop.pick_folder(tmp_path)


def test_only_one_dialog_at_a_time(tmp_path, monkeypatch):
    def run(cmd, **kw):
        with pytest.raises(desktop.DialogBusy):
            desktop.pick_folder(tmp_path)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert desktop.pick_folder(tmp_path) is None


def test_pick_endpoint_returns_a_path_and_does_not_switch(client, tmp_path, monkeypatch):
    target = tmp_path / "photos"
    target.mkdir()
    before = folders.current_media_dir()
    monkeypatch.setattr(desktop, "pick_folder", lambda initial, title="": target)
    r = client.post("/api/settings/media-folder/pick")
    assert r.status_code == 200 and r.json() == {"path": str(target.resolve())}
    assert folders.current_media_dir() == before
    monkeypatch.setattr(desktop, "pick_folder", lambda initial, title="": None)
    assert client.post("/api/settings/media-folder/pick").json() == {"path": None}


def test_pick_endpoint_rejects_the_apps_own_data_folder(client, monkeypatch):
    monkeypatch.setattr(desktop, "pick_folder", lambda initial, title="": settings.data_dir)
    r = client.post("/api/settings/media-folder/pick")
    assert r.status_code == 422 and "data folder" in r.json()["detail"]["message"]


def test_purge_old_deletes_only_old_log_files(tmp_path):
    old = tmp_path / "server.log.2026-11-01"
    new = tmp_path / "server.log"
    other = tmp_path / "notes.txt"
    for f in (old, new, other):
        f.write_text("x")
    three_days_ago = time.time() - 3 * 86400
    os.utime(old, (three_days_ago, three_days_ago))
    os.utime(other, (three_days_ago, three_days_ago))
    assert logs.purge_old(tmp_path, 2) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_setup_writes_to_the_log_file_and_keeps_two_days(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    stale = tmp_path / "logs" / "server.log.2026-01-01"
    stale.parent.mkdir()
    stale.write_text("old")
    os.utime(stale, (time.time() - 5 * 86400,) * 2)
    try:
        assert logs.setup() == tmp_path / "logs"
        assert not stale.exists()
        assert logs._handler.backupCount == settings.log_days == 2
        logging.getLogger("mco.test").info("hello log")
        logs._handler.flush()
        assert "hello log" in (tmp_path / "logs" / "server.log").read_text(encoding="utf-8")
    finally:
        logs.shutdown()


def test_browser_errors_reach_the_server_log(client, caplog):
    with caplog.at_level(logging.ERROR, logger="mco.client"):
        r = client.post("/api/log", json={"level": "error", "message": "boom at x.ts:1", "where": "#/packs"})
    assert r.status_code == 204
    assert "boom at x.ts:1" in caplog.text and "#/packs" in caplog.text
    assert client.post("/api/log", json={"message": "x" * 5000}).status_code == 422


def test_status_polls_are_kept_out_of_the_log():
    f = logs.QuietPolls()

    def access(method, path, status):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                 ("127.0.0.1:1", method, path, "1.1", status), None)

    assert f.filter(access("GET", "/api/media/scan", 200)) is False
    assert f.filter(access("GET", "/api/media?limit=1", 200)) is False
    assert f.filter(access("GET", "/api/media/scan", 500)) is True  # a failing poll is worth a line
    assert f.filter(access("POST", "/api/media/scan", 202)) is True  # a rescan the player asked for
    assert f.filter(access("GET", "/api/media?offset=0&limit=500&sort=added", 200)) is True
    assert f.filter(logging.LogRecord("mco.scanner", logging.INFO, "", 0, "Scan finished", None, None)) is True
