"""Things the local server does on the player's desktop: show a folder in the file manager and ask
for a folder with the system's own "Select folder" dialog.

The browser can't do either (a web page never learns a real folder path), and this server always
runs on the player's own machine, so it does them. Both put their window in front of the browser:
a window opened from a background process otherwise appears behind it and looks like nothing
happened.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("mco.desktop")

_pick_lock = threading.Lock()  # one folder dialog at a time

# Runs in its own Python process: Tk must own its thread, and a crash can't hurt the server.
_PICK_SCRIPT = r"""
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
path = filedialog.askdirectory(parent=root, title=sys.argv[2], initialdir=sys.argv[1], mustexist=True)
root.destroy()
sys.stdout.buffer.write((path or "").encode("utf-8"))
"""

PICK_TIMEOUT = 600  # seconds the dialog may stay open


class DialogBusy(Exception):
    """A folder dialog is already open."""


def pick_folder(initial: Path, title: str = "Choose your media folder") -> Path | None:
    """Show the system folder dialog and return the chosen folder, or None when it was cancelled.

    Raises DialogBusy if a dialog is already open and RuntimeError if no dialog can be shown.
    """
    if not _pick_lock.acquire(blocking=False):
        raise DialogBusy
    try:
        start = initial if initial.is_dir() else Path.home()
        shown = threading.Event()
        if sys.platform == "win32":  # the dialog of a background process opens behind the browser
            threading.Thread(target=_raise_window_titled, args=(title, shown), name="mco-pick-folder", daemon=True).start()
        try:
            r = subprocess.run(
                [sys.executable, "-c", _PICK_SCRIPT, str(start), title],
                capture_output=True, timeout=PICK_TIMEOUT, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return None
        except OSError as e:
            raise RuntimeError("Couldn't open the folder dialog. Type the folder's path instead.") from e
        finally:
            shown.set()
        if r.returncode != 0:
            log.warning("Folder dialog failed: %s", r.stderr.decode("utf-8", "replace").strip()[-500:])
            raise RuntimeError("Couldn't open the folder dialog. Type the folder's path instead.")
        chosen = r.stdout.decode("utf-8").strip()
        return Path(chosen) if chosen else None
    finally:
        _pick_lock.release()


def _raise_window(hwnd: int) -> None:
    """Windows: put a window in front of everything and give it the keyboard focus."""
    import ctypes

    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # on top of everything…
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # …without staying "always on top"
    # A background process may not take the keyboard focus by itself; borrowing the input
    # queue of whichever window has it now (the browser) is the documented way around that.
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    user32.AttachThreadInput(me, other, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(me, other, False)


def _raise_window_titled(title: str, done: threading.Event, timeout: float = 15.0) -> None:
    """Windows: wait for a window with this exact title (the folder dialog) and raise it."""
    import ctypes

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not done.is_set():
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
            _raise_window(hwnd)
            return
        time.sleep(0.1)


def _bring_explorer_to_front(folder: Path, known: set[int], timeout: float = 4.0) -> None:
    """Windows: wait for the new Explorer window and put it in front of the browser."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def explorer_windows() -> list[int]:
        found: list[int] = []

        def visit(hwnd, _):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value == "CabinetWClass" and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        return found

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = [h for h in explorer_windows() if h not in known]
        if new:
            _raise_window(new[0])
            return
        time.sleep(0.1)
    # No new window: Explorer reused one that already showed this folder. Raise the one named like it.
    for hwnd in explorer_windows():
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if folder.name and folder.name.lower() in title.value.lower():
            _raise_window(hwnd)
            return


def _explorer_window_ids() -> set[int]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: set[int] = set()

    def visit(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "CabinetWClass":
            found.add(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    return found


def open_folder(folder: Path) -> None:
    """Show ``folder`` in the OS file manager, in front of the browser. Raises RuntimeError if it can't."""
    if not folder.is_dir():
        raise RuntimeError("That folder isn't available right now. Check that its drive is connected.")
    d = str(folder)
    try:
        if sys.platform == "win32":
            known = _explorer_window_ids()
            os.startfile(d)  # type: ignore[attr-defined]
            threading.Thread(
                target=_bring_explorer_to_front, args=(folder, known), name="mco-open-folder", daemon=True
            ).start()
        elif sys.platform == "darwin":
            subprocess.run(["open", d], check=True, timeout=10)
        else:
            subprocess.run(["xdg-open", d], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError("Couldn't open the folder. Open it from your file manager instead.") from e
