"""The tagging pause (v0.9.0, test round 6 point 21): a brake on tagging without looking.

A player who keeps hitting one quick key (or random ones) moves through the cards far faster than
anyone can look at them. So the times of the paid tags are kept: when more than
``tagging.burst_tags`` of them land within ``tagging.burst_seconds``, a pause of
``tagging.cooldown_seconds`` starts. A tag sent during the pause is applied but pays nothing. The
page gets the pause with every tag reply (``cooldown_until``, ``must_type``), locks the quick keys
and counts down. A tag the page marks as typed ends the pause at once and is paid: typing a tag is
the deliberate act the pause asks for. The server cannot check that a tag was really typed, so the
unpaid pause is what holds for a quick key.

One tag every 1.5 seconds is 6 tags in 8 seconds and never starts a pause; a 7th paid tag within
8 seconds of the first does (faster than one tag per 1.33 seconds, kept up for 7 tags).

In memory, for the one local player: a restart forgets a pause, which is harmless. Bulk tagging
(one deliberate action on cards the player picked) is not counted and not paused.
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone

from ..game.config import get_config

_lock = threading.Lock()
_times: deque[float] = deque()
_until = 0.0


def reset() -> None:
    """Forget everything (tests call this between cases)."""
    global _until
    with _lock:
        _times.clear()
        _until = 0.0


def paused(typed: bool = False, now: float | None = None) -> bool:
    """Is a tag sent now unpaid because of the pause? A typed tag ends the pause (and is paid)."""
    global _until
    now = time.time() if now is None else now
    with _lock:
        if now >= _until:
            return False
        if typed:
            _until = 0.0
            _times.clear()
            return False
        return True


def note_paid(now: float | None = None) -> bool:
    """Call for every tag that was paid. True when this one started a pause."""
    global _until
    t = get_config().tagging
    if t.burst_tags <= 0 or t.cooldown_seconds <= 0:
        return False
    now = time.time() if now is None else now
    with _lock:
        _times.append(now)
        while _times and now - _times[0] > t.burst_seconds:
            _times.popleft()
        if len(_times) > t.burst_tags:
            _until = now + t.cooldown_seconds
            _times.clear()
            return True
    return False


def status(now: float | None = None) -> dict:
    """For every tag reply: `cooldown_until` (ISO time the pause ends, or None) and `must_type`
    (a pause is on: quick keys pay nothing until then, a typed tag ends it)."""
    now = time.time() if now is None else now
    with _lock:
        on = now < _until
        return {
            "cooldown_until": datetime.fromtimestamp(_until, tz=timezone.utc).isoformat() if on else None,
            "must_type": on,
        }
