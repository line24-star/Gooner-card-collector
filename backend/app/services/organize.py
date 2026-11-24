"""Tagging, categories, review and bulk tagging. Rewards only flush, so every function commits."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..game.config import get_config
from ..game.media import normalize_tag
from ..game.titles import normalize_title
from ..models import MediaItem, Tag, User
from . import tagpace
from .cards import card_out
from .rewards import best_owned, note_milestones, refund_tag, reward_review, reward_tag


class MediaNotFound(LookupError):
    pass


class TooManyItems(ValueError):
    pass


class NotPulled(PermissionError):
    """The card has not been pulled from a pack: only cards you own can be organized."""


def _present(db: Session, media_id: int) -> MediaItem:
    m = db.get(MediaItem, media_id)
    if m is None or m.missing:
        raise MediaNotFound(media_id)
    return m


def _get_or_create(db: Session, name: str, parent_id: int | None = None) -> Tag:
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        tag = Tag(name=name, parent_id=parent_id)
        db.add(tag)
        db.flush()
    elif parent_id is not None and tag.parent_id is None and tag.id != parent_id:
        tag.parent_id = parent_id
    return tag


def _tag_out(t: Tag) -> dict:
    return {"id": t.id, "name": t.name, "parent_id": t.parent_id}


def add_tag(db: Session, user: User, media_id: int, name: str, parent: str | None = None,
            typed: bool = False) -> dict:
    """Put a tag on a card the player owns and pay for it.

    v0.9.0: `typed` says the tag was typed (not a quick key). During the tagging pause
    (services/tagpace.py) a tag is applied but pays nothing (`paused` true in the reply); a typed
    tag ends the pause and is paid. The reply always carries `cooldown_until` and `must_type`."""
    name = normalize_tag(name)
    parent_name = normalize_tag(parent) if parent else None
    media = _present(db, media_id)
    if best_owned(db, user, media_id) is None:
        raise NotPulled(media_id)
    parent_tag = _get_or_create(db, parent_name) if parent_name and parent_name != name else None
    tag = _get_or_create(db, name, parent_tag.id if parent_tag else None)
    awarded, held, milestones = 0, False, []
    if tag not in media.tags:
        held = tagpace.paused(typed)
        media.tags.append(tag)
        db.flush()
        awarded = reward_tag(db, user, media, tag.id, pay=not held)
        # a tag moves completeness only in the older modes ("tagged", "weighted"); a milestone it
        # reaches there becomes claimable, as after a pack
        milestones = note_milestones(db, user)
    db.commit()
    if awarded > 0:
        tagpace.note_paid()
    return {
        "tag": _tag_out(tag),
        "coins_awarded": awarded,
        "paused": held,  # true: this tag was applied during the pause and paid nothing
        **tagpace.status(),
        "milestones": milestones,  # newly claimable, as in the open-pack reply (empty in "slots" mode)
        "coins": user.coins,
        "card": card_out(db, user, media),
    }


def remove_tag(db: Session, user: User, media_id: int, tag_id: int) -> dict:
    """Take a tag off a card. v0.9.0: what that tag paid on that card is taken back (`refunded`),
    as far as the balance goes; the same tag, or the right one, pays again when it is added."""
    media = _present(db, media_id)
    tag = db.get(Tag, tag_id)
    refunded = 0
    if tag is not None and tag in media.tags:
        media.tags.remove(tag)
        db.flush()
        refunded = refund_tag(db, user, media, tag.id)
    db.commit()
    return {"refunded": refunded, "coins": user.coins, "card": card_out(db, user, media)}


def set_title(db: Session, user: User, media_id: int, title: str | None) -> dict:
    """Give a card you own your own name, or clear it (None or empty). Returns the card.

    Only the name in the database changes: the file on disk keeps its name.
    """
    cleaned = normalize_title(title)  # ValueError for a bad title, before anything is looked up
    media = _present(db, media_id)
    if best_owned(db, user, media_id) is None:
        raise NotPulled(media_id)
    media.title = cleaned
    db.commit()
    return card_out(db, user, media)


def review(db: Session, user: User, media_id: int) -> dict:
    media = _present(db, media_id)
    best = best_owned(db, user, media_id)
    awarded = reward_review(db, user, media_id, best) if best else 0
    db.commit()
    return {"coins_awarded": awarded, "coins": user.coins, "card": card_out(db, user, media)}


def bulk_tag(db: Session, user: User, media_ids: list[int], name: str) -> dict:
    """One tag on many cards. v0.9.0: every tag placed this way pays `tagging.bulk_multiplier` of
    the normal rate (a quarter), at least 1 coin for a tag that pays at all."""
    name = normalize_tag(name)
    ids = list(dict.fromkeys(media_ids))
    if len(ids) > get_config().limits.bulk_max_items:
        raise TooManyItems(len(ids))
    tag = _get_or_create(db, name)
    applied, awarded = 0, 0
    for mid in ids:
        media = db.get(MediaItem, mid)
        if media is None or media.missing or best_owned(db, user, mid) is None:
            continue  # gone, or not pulled yet: only cards you own can be organized
        if tag not in media.tags:
            media.tags.append(tag)
            db.flush()
            awarded += reward_tag(db, user, media, tag.id, factor=get_config().tagging.bulk_multiplier)
        applied += 1
    note_milestones(db, user)
    db.commit()
    # Bulk tagging is one deliberate action on cards the player picked: it is not counted by the
    # tagging pause and not held by it. The pause's state is in the reply all the same.
    return {"applied": applied, "coins_awarded": awarded, "coins": user.coins, **tagpace.status()}
