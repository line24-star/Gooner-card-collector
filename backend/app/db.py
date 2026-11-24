from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------- schema migrations
# Lightweight, SQLite-friendly migrations tracked in PRAGMA user_version.
# Fresh databases are created at the latest schema by create_all() and stamped directly.
# To change an existing table: append a step here (never edit old steps) and update models.py.
# New tables need no step; create_all() adds them.

MIGRATIONS: list[list[str]] = [
    # v1 → v2: economy groundwork
    [
        "ALTER TABLE users ADD COLUMN packs_opened INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN streak_days INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_active_day VARCHAR(10)",
        "ALTER TABLE transactions ADD COLUMN ref VARCHAR(64) NOT NULL DEFAULT ''",
        "ALTER TABLE transactions ADD COLUMN dedupe_key VARCHAR(128)",
        "ALTER TABLE transactions ADD COLUMN balance_after INTEGER NOT NULL DEFAULT 0",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_transactions_dedupe_key ON transactions (dedupe_key)",
    ],
    # v2 → v3: media metadata, missing-file flag, pack idempotency (app_state is a new table)
    [
        "ALTER TABLE media_items ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE media_items ADD COLUMN width INTEGER",
        "ALTER TABLE media_items ADD COLUMN height INTEGER",
        "ALTER TABLE media_items ADD COLUMN format VARCHAR(16)",
        "ALTER TABLE media_items ADD COLUMN duration_s REAL",
        "ALTER TABLE media_items ADD COLUMN mtime REAL NOT NULL DEFAULT 0",
        "ALTER TABLE media_items ADD COLUMN missing INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS ix_media_items_missing ON media_items (missing)",
        "ALTER TABLE packs ADD COLUMN idem_key VARCHAR(64)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_packs_idem_key ON packs (idem_key)",
    ],
    # v3 → v4 (v0.3.0): sealed packs in an inventory
    [
        "ALTER TABLE packs ADD COLUMN pack_type VARCHAR(32)",
        "ALTER TABLE packs ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'bought'",
        "ALTER TABLE packs ADD COLUMN cost INTEGER NOT NULL DEFAULT 0",
    ],
    # v4 → v5 (v0.4.0): the market. market_cards and market_news are new tables (create_all).
    [
        "ALTER TABLE media_items ADD COLUMN print_run INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE media_items ADD COLUMN popularity REAL NOT NULL DEFAULT 1.0",
    ],
    # v5 → v6 (v0.5.0): the player's own name for a card
    [
        "ALTER TABLE media_items ADD COLUMN title VARCHAR(80)",
    ],
    # v6 → v7 (v0.8.0): every card has a permanent number. Existing cards are numbered in the order
    # they were scanned (ingested time, then id), missing files included. New cards get theirs when
    # they are stored (models.py).
    [
        "ALTER TABLE media_items ADD COLUMN number INTEGER",
        "UPDATE media_items SET number = (SELECT rn FROM (SELECT id, ROW_NUMBER() OVER "
        "(ORDER BY ingested_at, id) AS rn FROM media_items) AS r WHERE r.id = media_items.id) "
        "WHERE number IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_media_items_number ON media_items (number)",
        # The same version changed what a Foil, Holo or Special copy is worth. A collector's offer
        # still open was made at the old prices, so it is withdrawn (the next price update makes a new one).
        "UPDATE app_state SET value = json_remove(value, '$.offer') WHERE key = 'market' AND json_valid(value)",
    ],
]
# New tables need no step here: create_all() builds them (e.g. set_credits, added in v0.2.0).
SCHEMA_VERSION = len(MIGRATIONS) + 1


def migrate(eng: Engine) -> int:
    """Bring the database to SCHEMA_VERSION. Returns the resulting version."""
    from . import models  # noqa: F401  (register models)

    fresh = "users" not in inspect(eng).get_table_names()
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        if fresh:
            conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
            return SCHEMA_VERSION
        version = conn.execute(text("PRAGMA user_version")).scalar() or 1
        for step in MIGRATIONS[version - 1 :]:
            for sql in step:
                try:
                    conn.execute(text(sql))
                except OperationalError as e:
                    # create_all() above may have just built a table at the latest shape.
                    if "duplicate column name" not in str(e):
                        raise
            version += 1
        conn.execute(text(f"PRAGMA user_version = {version}"))
        return version


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    migrate(engine)
