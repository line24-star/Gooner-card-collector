"""The watched media folder as a runtime setting.

The effective folder is an in-memory override (persisted in AppState under ``media_dir``) or, when
none is set, ``settings.media_dir`` (read live, never cached). Changing it restarts the scanner;
cards from the old folder turn "missing" on the next scan and come back if the folder is switched
back.
"""

import os
import threading
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ROOT, settings
from ..models import AppState, MediaItem

KEY = "media_dir"
_override: Path | None = None
_lock = threading.RLock()  # serializes folder switches


class FolderChangeNeedsConfirm(Exception):
    """Changing the folder would hide existing cards; the caller must confirm first."""

    def __init__(self, count: int):
        super().__init__(f"{count} cards would be hidden")
        self.count = count


def current_media_dir() -> Path:
    return _override if _override is not None else settings.media_dir


def default_media_dir() -> Path:
    return settings.media_dir


def reset_override() -> None:
    """Forget the in-memory override (tests call this between cases)."""
    global _override
    _override = None


def media_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.missing.is_(False))) or 0


def load_override(db: Session) -> None:
    """Read the saved folder at startup. A saved folder that no longer exists is ignored (the
    default is used) but kept, so a drive that is plugged in later is picked up on the next start."""
    global _override
    row = db.get(AppState, KEY)
    if row and row.value and Path(row.value).is_dir():
        _override = Path(row.value)
    else:
        _override = None


def _inside(child: Path, parent: Path) -> bool:
    return child.is_relative_to(parent)


def _validate(path: str) -> Path:
    if not path or not path.strip():
        raise ValueError("Choose a folder.")
    p = Path(path.strip())
    if not p.is_absolute():
        raise ValueError("Use the full path to the folder, e.g. C:\\Users\\you\\Pictures.")
    try:
        p = p.resolve()
    except OSError:
        raise ValueError("That folder can't be used.") from None
    if not p.is_dir():
        raise ValueError("That folder doesn't exist.")
    if not os.access(p, os.R_OK | os.X_OK):
        raise ValueError("That folder can't be read.")
    for r in (settings.data_dir, ROOT / "data"):
        r = r.resolve()
        if _inside(p, r) or _inside(r, p):
            raise ValueError("Pick a folder that isn't part of the app's own data folder.")
    for r in (settings.profiles_dir, ROOT / "profiles"):
        r = r.resolve()
        if _inside(p, r) or _inside(r, p):
            raise ValueError("Pick a folder that isn't part of the app's profiles folder.")
    return p


def _saved(db: Session) -> str | None:
    row = db.get(AppState, KEY)
    return row.value if row is not None else None


def saved_unavailable(db: Session) -> str | None:
    """The saved folder path when it is saved but not in use (it was unavailable at startup)."""
    saved = _saved(db)
    return saved if saved and _override is None else None


def _persist(db: Session, value: str | None) -> None:
    row = db.get(AppState, KEY)
    if value is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AppState(key=KEY, value=value))
    else:
        row.value = value
    db.commit()


def _switch(db: Session, new: Path | None, target: Path) -> None:
    """Persist ``new`` (None = default), reset the free first import (the new folder's library is
    imported free), then restart the scanner on ``target``. If the restart fails, restore exactly the
    previous in-memory override, the previously persisted value and the first-import state."""
    from . import scanner  # late: scanner imports ingest, which imports this module

    global _override
    prev_mem = _override
    prev_saved = _saved(db)
    prev_import = {k: r.value for k in (scanner.INITIAL_SCAN_KEY, scanner.INITIAL_UNTIL_KEY) if (r := db.get(AppState, k))}
    _persist(db, str(new) if new is not None else None)
    _override = new
    scanner.reset_initial_import(db)  # importing the new folder's library pays nothing
    db.commit()
    try:
        scanner.stop_background()
        if scanner.start_background(target) is False:
            raise OSError("the folder watcher could not start")
    except Exception as e:
        _override = prev_mem
        for k, v in prev_import.items():
            db.merge(AppState(key=k, value=v))
        _persist(db, prev_saved)
        try:
            scanner.stop_background()
            scanner.start_background(current_media_dir())
        except Exception:
            pass
        raise ValueError("Couldn't start watching that folder. Your previous folder is still in use.") from e


def _check_confirm(db: Session, confirm: bool) -> None:
    n = media_count(db)
    if n and not confirm:
        raise FolderChangeNeedsConfirm(n)


def set_media_dir(db: Session, path: str, *, confirm: bool) -> Path:
    with _lock:
        new = _validate(path)
        if new == current_media_dir().resolve():
            return current_media_dir()
        _check_confirm(db, confirm)
        _switch(db, new, new)
        return new


def reset_media_dir(db: Session, *, confirm: bool) -> Path:
    with _lock:
        default = default_media_dir()
        if _override is None:
            if _saved(db) is not None:  # saved but unavailable at startup: forget it
                _persist(db, None)
            return default
        _check_confirm(db, confirm)
        default.mkdir(parents=True, exist_ok=True)
        _switch(db, None, default)
        return default


def open_media_dir() -> None:
    """Show the folder in the OS file manager, in front of the browser."""
    from . import desktop

    cur = current_media_dir()
    if not cur.is_dir():
        raise RuntimeError("The media folder isn't available right now. Check that its drive is connected.")
    desktop.open_folder(cur)


def pick_media_dir() -> Path | None:
    """Ask for a folder with the system dialog. Returns it (validated), or None when cancelled.
    Raises ValueError for a folder that can't be used. Does not switch to it."""
    from . import desktop

    chosen = desktop.pick_folder(current_media_dir())
    return _validate(str(chosen)) if chosen is not None else None
