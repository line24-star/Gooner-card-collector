"""A small in-memory token bucket (single local user, so one process-wide bucket is enough)."""

import threading
import time
from collections.abc import Callable

from ..game.config import get_config


class TokenBucket:
    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rate
        self.burst = burst
        self._clock = clock
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last time)
        self._lock = threading.Lock()

    def allow(self, key: str, cost: int = 1) -> bool:
        with self._lock:
            now = self._clock()
            tokens, last = self._state.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            ok = tokens >= cost
            if ok:
                tokens -= cost
            self._state[key] = (tokens, now)
            return ok


def _from_config() -> TokenBucket:
    lim = get_config().limits
    return TokenBucket(lim.organize_per_second, lim.organize_burst)


organize_bucket: TokenBucket = _from_config()


def reset_organize_bucket() -> None:
    """Fresh bucket from the current config (used by tests)."""
    global organize_bucket
    organize_bucket = _from_config()
