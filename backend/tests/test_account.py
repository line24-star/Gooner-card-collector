"""The player's account page API."""

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.game.config import get_config
from app.main import app
from app.models import CollectionEntry, MediaItem
from app.routers.account import clean_name
from app.services import desktop
from app.services.wallet import credit, debit, local_user

cfg = get_config()


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


def test_account_shows_name_balance_and_lifetime_totals(client):
    with SessionLocal() as db:
        u = local_user(db)
        credit(db, u, 40, "tag")
        debit(db, u, 210, "pack")
        m = MediaItem(path="a.png", sha256="a" * 64, kind="image")
        gone = MediaItem(path="b.png", sha256="b" * 64, kind="image", missing=True)
        db.add_all([m, gone])
        db.flush()
        db.add_all([CollectionEntry(user_id=u.id, media_id=m.id, treatment="foil", copies=2),
                    CollectionEntry(user_id=u.id, media_id=gone.id, treatment="normal", copies=1)])
        db.commit()
    a = client.get("/api/account").json()
    assert a["name"] == "Player" and a["since"]
    assert a["coins"] == cfg.starting_coins + 40 - 210
    assert a["earned"] == cfg.starting_coins + 40 and a["spent"] == 210
    assert {r["reason"]: r["total"] for r in a["by_reason"]} == {"starting_grant": cfg.starting_coins, "tag": 40, "pack": -210}
    assert a["cards_total"] == 1 and a["cards_owned"] == 1  # the missing card is not counted
    assert a["media_folder"] and a["log_folder"].endswith("logs")


def test_rename_trims_and_validates(client):
    assert client.put("/api/account", json={"name": "  Jo   the  Collector "}).json()["name"] == "Jo the Collector"
    assert client.get("/api/account").json()["name"] == "Jo the Collector"
    assert client.put("/api/account", json={"name": "   "}).status_code == 422
    assert client.put("/api/account", json={"name": "x" * 33}).status_code == 422
    with pytest.raises(ValueError):
        clean_name("")


def test_open_logs_shows_the_log_folder(client, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop, "open_folder", lambda p: opened.append(p))
    assert client.post("/api/account/logs/open").status_code == 204
    assert opened and opened[0].name == "logs"
