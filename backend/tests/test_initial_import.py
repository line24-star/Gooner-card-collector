"""v0.1.1: the first import of a library never pays, and the player starts with welcome coins."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.game.config import get_config
from app.models import AppState, Transaction, User
from app.services import folders, scanner, wallet
from app.services.scanner import INITIAL_SCAN_KEY, MediaWatcher, full_scan
from tests.test_scanner import coins, count_rows, end_first_import, make_png, make_pngs

cfg = get_config()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def tmp_media(tmp_path, monkeypatch):
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    return d


def _close_window(db) -> None:
    end_first_import()
    db.expire_all()


def test_library_copied_in_over_several_watcher_batches_is_all_free(tmp_media, db):
    full_scan(tmp_media)  # app started on an empty folder
    w = MediaWatcher(tmp_media, stable_interval=0)
    for batch in range(4):  # a big copy reaches the watcher as several batches
        w._process(set(make_pngs(tmp_media, 5, start=batch * 5)), False)
    assert count_rows() == 20
    assert coins(db) == cfg.starting_coins


def test_files_after_the_quiet_period_pay(tmp_media, db):
    full_scan(tmp_media)
    w = MediaWatcher(tmp_media, stable_interval=0)
    w._process(set(make_pngs(tmp_media, 5)), False)
    _close_window(db)
    w._process({make_png(tmp_media / "later.png", (200, 100, 50))}, False)
    assert coins(db) == cfg.starting_coins + cfg.ingest.image


def test_watcher_files_right_after_the_first_full_scan_are_free(tmp_media, db):
    make_pngs(tmp_media, 5)
    full_scan(tmp_media)  # the startup scan finds part of the library…
    w = MediaWatcher(tmp_media, stable_interval=0)
    w._process(set(make_pngs(tmp_media, 5, start=5)), False)  # …the rest of the copy lands after it
    assert coins(db) == cfg.starting_coins


def test_an_old_finished_import_stays_closed(tmp_media, db):
    """A database from v0.1.0 has the flag but no window: new files pay as before."""
    db.merge(AppState(key=INITIAL_SCAN_KEY, value="1"))
    db.commit()
    make_png(tmp_media / "new.png", (9, 9, 9))
    full_scan(tmp_media)
    assert coins(db) == cfg.starting_coins + cfg.ingest.image


def test_switching_to_a_new_folder_imports_it_free(tmp_path, tmp_media, db, monkeypatch):
    make_pngs(tmp_media, 3)
    full_scan(tmp_media)
    _close_window(db)
    other = tmp_path / "library"
    make_pngs(other, 6, start=10)
    started = []
    monkeypatch.setattr(scanner, "stop_background", lambda: None)
    monkeypatch.setattr(scanner, "start_background", lambda d: started.append(d) or True)
    try:
        folders.set_media_dir(db, str(other), confirm=True)
        db.expire_all()
        assert db.get(AppState, INITIAL_SCAN_KEY) is None  # the new folder gets its own free import
        full_scan(other)
    finally:
        folders.reset_override()
    assert started and count_rows(missing=False) == 6
    assert coins(db) == cfg.starting_coins


def test_welcome_coins_buy_the_first_pack(db):
    assert cfg.starting_coins >= cfg.packs["standard"].base_cost
    assert wallet.local_user(db).coins == cfg.starting_coins


def test_existing_player_is_topped_up_to_the_welcome_amount_once(db):
    u = User(username="local", password_hash="", coins=0)
    db.add(u)
    db.flush()
    wallet.credit(db, u, 200, "starting_grant", dedupe_key=f"starting-grant:{u.id}")  # a v0.1.0 player
    db.commit()
    wallet.top_up_starting_coins(db)
    wallet.top_up_starting_coins(db)
    db.expire_all()
    assert db.get(User, u.id).coins == cfg.starting_coins
    grants = db.scalars(select(Transaction).where(Transaction.reason == "starting_grant")).all()
    assert sum(t.amount for t in grants) == cfg.starting_coins


def test_top_up_never_creates_a_player(db):
    wallet.top_up_starting_coins(db)
    assert db.scalars(select(User)).first() is None


def test_config_endpoint_shows_the_welcome_amount():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/economy/config").json()["starting_coins"] == cfg.starting_coins
