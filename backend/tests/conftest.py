"""Point the app at a throwaway DB + media folder before any app module is imported."""

import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="mco-test-"))
(_tmp / "data").mkdir()
os.environ["MCO_DATA_DIR"] = str(_tmp / "data")
os.environ["MCO_MEDIA_DIR"] = str(_tmp / "media")
os.environ["MCO_WATCH_MEDIA"] = "false"  # tests drive the scanner directly
os.environ["MCO_LOG_TO_FILE"] = "false"  # tests that cover the log file turn it on themselves
os.environ["MCO_DATABASE_URL"] = f"sqlite:///{(_tmp / 'data' / 'test.db').as_posix()}"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create the schema once per test run."""
    from app.db import engine, migrate

    migrate(engine)


@pytest.fixture(autouse=True)
def fresh_db(_schema):
    """Every test starts with empty tables (much faster than rebuilding the schema)."""
    from app.db import Base, engine

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    from app.services import ratelimit

    from app.services import folders

    folders.reset_override()  # a folder change in one test must not leak into the next
    ratelimit.reset_organize_bucket()  # earlier tests must not exhaust the organize rate limit
    from app.services import tagpace

    tagpace.reset()  # a tagging pause started in one test must not hold the next one's tags
    yield


@pytest.fixture(autouse=True)
def flat_fuse(request, monkeypatch):
    """Fusing takes the base number of copies in every test, whatever a card's (random) price is.

    The price steps of v0.7.0 have their own tests, marked `price_steps`, which keep the live config."""
    if "price_steps" in request.keywords:
        return
    from app.game.config import get_config

    monkeypatch.setattr(get_config().fuse, "price_steps", [])


@pytest.fixture(autouse=True)
def upgrades_switch(request, monkeypatch):
    """Upgrade attempts are switched off in the live config (v0.7.1). The tests of the attempt
    itself are marked `upgrades_on` and run with the switch on; every other test keeps it off."""
    if "upgrades_on" not in request.keywords:
        return
    from app.game.config import get_config

    monkeypatch.setattr(get_config().upgrades, "enabled", True)
