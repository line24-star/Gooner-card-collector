"""Core entities from design doc section 5. Starting skeleton; extend as phases land."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


media_tags = Table(
    "media_tags",
    Base.metadata,
    Column("media_id", ForeignKey("media_items.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    coins: Mapped[int] = mapped_column(Integer, default=0)
    packs_opened: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_active_day: Mapped[str | None] = mapped_column(String(10))  # ISO date of last check-in
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MediaItem(Base):
    __tablename__ = "media_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # "image" | "video"
    # Base look only. What the user OWNS (and its treatment) lives in CollectionEntry.
    treatment: Mapped[str] = mapped_column(String(16), default="normal")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(String(16))
    duration_s: Mapped[float | None] = mapped_column(Float)
    mtime: Mapped[float] = mapped_column(Float, default=0)
    missing: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # v0.4.0, the market: copies "issued" (0 = not issued yet) and the demand for the card itself.
    print_run: Mapped[int] = mapped_column(Integer, default=0)
    popularity: Mapped[float] = mapped_column(Float, default=1.0)
    # v0.5.0: the player's own name for the card (empty = named after the file, see game/titles.py).
    title: Mapped[str | None] = mapped_column(String(80))
    # v0.8.0: the card's permanent number, shown as "#000123". Given once, when the row is first
    # stored (the next free number, from 1), and never changed: a missing file keeps its number.
    number: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    tags: Mapped[list["Tag"]] = relationship(secondary=media_tags, back_populates="media")


@event.listens_for(Session, "before_flush")
def _number_new_cards(session: Session, flush_context, instances) -> None:
    """Every new card gets the next free number, whatever code path stores it (scan, tests, tools)."""
    new = [o for o in session.new if isinstance(o, MediaItem) and o.number is None]
    if not new:
        return
    taken = [o.number for o in session.new if isinstance(o, MediaItem) and o.number is not None]
    last = session.connection().execute(select(func.coalesce(func.max(MediaItem.number), 0))).scalar() or 0
    last = max([last, *taken])
    for o in new:
        last += 1
        o.number = last


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id"))
    media: Mapped[list[MediaItem]] = relationship(secondary=media_tags, back_populates="tags")


class Transaction(Base):
    """Coin ledger. Every balance change is one row (the audit trail for the economy).

    dedupe_key makes one-time rewards idempotent: e.g. "tag:12:5" can only ever pay once,
    so removing and re-adding a tag, double-clicks, or retries can't farm coins.
    """

    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    ref: Mapped[str] = mapped_column(String(64), default="")  # e.g. "media:12", "pack:3"
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Pack(Base):
    __tablename__ = "packs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idem_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    contents_json: Mapped[str] = mapped_column(Text, default="[]")
    # v0.3.0: a pack is bought (or claimed) sealed and opened later. Sealed = pack_type set and
    # opened_at empty. Rows from before v0.3.0 have no pack_type and are all opened.
    pack_type: Mapped[str | None] = mapped_column(String(32))
    # "bought" | "free" (the daily free pack) | v0.9.0, a reward: "milestone", "goal", "daily_goal",
    # "mastery", "set", "onboarding", "tour". Everything but "bought" cost nothing.
    source: Mapped[str] = mapped_column(String(16), default="bought")
    cost: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AppState(Base):
    """Small key/value store for app-level state (e.g. first-scan-done flag)."""

    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TutorialProgress(Base):
    """Which onboarding tours / tutorials have been finished (or skipped).

    user_id is nullable until auth lands (single local user); then it becomes required.
    """

    __tablename__ = "tutorial_progress"
    __table_args__ = (UniqueConstraint("user_id", "tutorial_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    tutorial_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # "completed" | "skipped"
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MarketCard(Base):
    """A card on the market: its own price level around fair value, and a short price history."""

    __tablename__ = "market_cards"
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id"), primary_key=True)
    factor: Mapped[float] = mapped_column(Float, default=1.0)
    history: Mapped[str] = mapped_column(Text, default="[]")  # JSON: Normal price at the last ticks


class MarketNews(Base):
    __tablename__ = "market_news"
    id: Mapped[int] = mapped_column(primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    media_ids: Mapped[str] = mapped_column(Text, default="[]")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SetCredit(Base):
    """A card that already counted toward a category-set reward at a treatment.

    Each (card, treatment) pays into a set reward once, whatever tag the set is built on, so
    putting a new tag on the same cards can't claim their reward again.
    """

    __tablename__ = "set_credits"
    __table_args__ = (UniqueConstraint("user_id", "media_id", "treatment"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id"), index=True)
    treatment: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TagSlot(Base):
    """A card's paid-tag slots (v0.9.0). The first tag on a card pays the most, the next ones less:
    slot 0, 1, 2, ... A removed tag gives its coins back and leaves its slot free; the next tag on
    the card takes the lowest free slot and is paid at most what was taken back, so removing and
    adding tags never gains a coin.

    tag_id: the tag holding the slot, or None while the slot is free. amount: what the slot's tag
    was paid; for a free slot, what was taken back (the most its next tag can be paid). paid_at:
    when the slot was first paid: a tag that refills it counts for that day's goals, not again.
    Cards tagged before v0.9.0 get their rows from the ledger the first time they are touched."""

    __tablename__ = "tag_slots"
    __table_args__ = (UniqueConstraint("user_id", "media_id", "slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)
    tag_id: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CollectionEntry(Base):
    """A card the user owns: one media item at one treatment, possibly several copies."""

    __tablename__ = "collection_entries"
    __table_args__ = (UniqueConstraint("user_id", "media_id", "treatment"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id"), index=True)
    treatment: Mapped[str] = mapped_column(String(16))
    copies: Mapped[int] = mapped_column(Integer, default=0)
    first_pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
