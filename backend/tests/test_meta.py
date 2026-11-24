from fastapi.testclient import TestClient

from app.game.config import get_config
from app.main import app


def test_treatments_are_ordered_and_sane():
    tiers = get_config().tiers()
    mults = [t.multiplier for t in tiers]
    assert mults == sorted(mults) and mults[0] == 1.0
    assert tiers[0].id == "normal"


def test_treatments_endpoint_odds_sum_to_one():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        data = c.get("/api/meta/treatments").json()
    assert [t["id"] for t in data] == ["normal", "foil", "holo", "special"]
    assert abs(sum(t["pack_odds"] for t in data) - 1) < 1e-9
