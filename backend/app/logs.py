"""Server log files: ``<data_dir>/logs/server.log``, a new file each day, old days deleted.

``settings.log_days`` (default 2) is how many days are kept. Everything the server logs goes in:
requests (uvicorn's access log, minus the page's own status polls), scanner and watcher events, errors with tracebacks, and errors the
browser reports through ``POST /api/log``.
"""

import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .config import settings

FILE_NAME = "server.log"
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
# uvicorn's own loggers don't propagate to the root logger, so they get the handler directly.
_LOGGERS = ("", "uvicorn", "uvicorn.access")
_handler: logging.Handler | None = None


def log_dir() -> Path:
    return settings.data_dir / "logs"


def purge_old(directory: Path, days: int, now: float | None = None) -> int:
    """Delete log files last written more than ``days`` days ago. Returns how many were deleted."""
    cutoff = (now if now is not None else time.time()) - days * 86400
    removed = 0
    for f in directory.glob(f"{FILE_NAME}*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass  # in use or already gone; the next start tries again
    return removed


# The page asks these every couple of seconds; logging each answer would bury everything else.
_POLL_PATHS = ("/api/media/scan", "/api/media?limit=1", "/api/health")


class QuietPolls(logging.Filter):
    """Drops successful access-log lines of the page's status polls. Failures are still logged."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method, path, status = args[1], str(args[2]), args[4]
        return not (method == "GET" and status == 200 and path in _POLL_PATHS)


class _DailyHandler(TimedRotatingFileHandler):
    def doRollover(self) -> None:
        super().doRollover()
        purge_old(Path(self.baseFilename).parent, settings.log_days)


def setup() -> Path | None:
    """Start logging to the file (once per process). Returns the folder, or None if it can't be used."""
    global _handler
    directory = log_dir()
    if _handler is not None:
        return directory
    try:
        directory.mkdir(parents=True, exist_ok=True)
        purge_old(directory, settings.log_days)
        handler = _DailyHandler(directory / FILE_NAME, when="midnight", backupCount=settings.log_days, encoding="utf-8")
    except OSError:
        logging.getLogger("mco.logs").warning("Could not open the log file in %s", directory, exc_info=True)
        return None
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.setLevel(logging.INFO)
    handler.addFilter(QuietPolls())
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
    _handler = handler
    logging.getLogger("mco.logs").info("Logging to %s (kept %d days)", directory, settings.log_days)
    return directory


def shutdown() -> None:
    """Detach and close the file handler (tests, and a clean stop)."""
    global _handler
    if _handler is None:
        return
    for name in _LOGGERS:
        logging.getLogger(name).removeHandler(_handler)
    _handler.close()
    _handler = None
