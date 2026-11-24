"""Server lifecycle: browser-presence tracking and graceful shutdown.

The frontend holds a WebSocket open to /api/ws/presence for as long as a tab is open.
When the last tab disconnects, a grace timer starts (so page reloads / HMR don't kill
the server); if nobody reconnects before it expires, the server shuts itself down.
"""

import asyncio
import logging
import signal
from collections.abc import Callable

log = logging.getLogger("mco.lifecycle")

ShutdownCallback = Callable[[str], None]


def default_shutdown(reason: str) -> None:
    """Fallback when not run via the launcher: behave like Ctrl+C so uvicorn exits cleanly."""
    log.info("Shutting down: %s", reason)
    signal.raise_signal(signal.SIGINT)


class PresenceMonitor:
    def __init__(self, on_idle: ShutdownCallback, grace_seconds: float, enabled: bool = True):
        self._on_idle = on_idle
        self.grace_seconds = grace_seconds
        self.enabled = enabled
        self.clients = 0
        self._timer: asyncio.Task | None = None

    def connect(self) -> None:
        self.clients += 1
        self._cancel_timer()

    def disconnect(self) -> None:
        self.clients = max(0, self.clients - 1)
        if self.clients == 0 and self.enabled:
            self._cancel_timer()
            self._timer = asyncio.create_task(self._countdown())

    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _countdown(self) -> None:
        log.info("No browser connected; shutting down in %ss unless one reconnects", self.grace_seconds)
        await asyncio.sleep(self.grace_seconds)
        if self.clients == 0:
            self._on_idle("all browser tabs disconnected")
