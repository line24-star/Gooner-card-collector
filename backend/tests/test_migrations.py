from sqlalchemy import create_engine, inspect, text

from app.db import SCHEMA_VERSION, migrate

OLD_SCHEMA = [  # tables as shipped before the economy groundwork (schema v1)
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, "
    "password_hash VARCHAR(255), coins INTEGER, created_at DATETIME)",
    "CREATE TABLE transactions (id INTEGER PRIMARY KEY, user_id INTEGER, amount INTEGER, "
    "reason VARCHAR(64), created_at DATETIME)",
    "INSERT INTO users (username, password_hash, coins) VALUES ('local', '', 42)",
]


def test_migrates_v1_database_and_keeps_data(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with eng.begin() as c:
        for sql in OLD_SCHEMA:
            c.execute(text(sql))
    assert migrate(eng) == SCHEMA_VERSION
    cols = {c["name"] for c in inspect(eng).get_columns("users")}
    assert {"packs_opened", "streak_days", "last_active_day"} <= cols
    assert "collection_entries" in inspect(eng).get_table_names()
    with eng.begin() as c:
        assert c.execute(text("SELECT coins FROM users")).scalar() == 42
    assert migrate(eng) == SCHEMA_VERSION  # running again is a no-op


V2_SCHEMA = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, "
    "password_hash VARCHAR(255), coins INTEGER, packs_opened INTEGER NOT NULL DEFAULT 0, "
    "streak_days INTEGER NOT NULL DEFAULT 0, last_active_day VARCHAR(10), created_at DATETIME)",
    "CREATE TABLE media_items (id INTEGER PRIMARY KEY, path TEXT, sha256 VARCHAR(64) UNIQUE, "
    "kind VARCHAR(8), treatment VARCHAR(16), ingested_at DATETIME)",
    "CREATE TABLE packs (id INTEGER PRIMARY KEY, user_id INTEGER, contents_json TEXT, "
    "opened_at DATETIME, created_at DATETIME)",
    "CREATE TABLE transactions (id INTEGER PRIMARY KEY, user_id INTEGER, amount INTEGER, "
    "reason VARCHAR(64), ref VARCHAR(64) NOT NULL DEFAULT '', dedupe_key VARCHAR(128), "
    "balance_after INTEGER NOT NULL DEFAULT 0, created_at DATETIME)",
    "PRAGMA user_version = 2",
    "INSERT INTO media_items (path, sha256, kind, treatment) VALUES ('a.jpg', 'abc', 'image', 'normal')",
]


def test_migrates_v2_to_v3_keeps_media(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'v2.db').as_posix()}")
    with eng.begin() as c:
        for sql in V2_SCHEMA:
            c.execute(text(sql))
    assert migrate(eng) == SCHEMA_VERSION == 7
    insp = inspect(eng)
    cols = {c["name"] for c in insp.get_columns("media_items")}
    assert {"size_bytes", "width", "height", "format", "duration_s", "mtime", "missing", "title", "number"} <= cols
    assert "idem_key" in {c["name"] for c in insp.get_columns("packs")}
    assert "app_state" in insp.get_table_names()
    with eng.begin() as c:
        row = c.execute(text("SELECT path, missing, size_bytes, mtime FROM media_items")).one()
        assert tuple(row) == ("a.jpg", 0, 0, 0)
    assert migrate(eng) == SCHEMA_VERSION  # running it again changes nothing


def test_fresh_database_is_stamped_latest(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'new.db').as_posix()}")
    assert migrate(eng) == SCHEMA_VERSION
