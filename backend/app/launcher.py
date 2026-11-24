"""One-command launcher used by start.bat / start-dev.bat.

Runs the FastAPI backend in-process and the Vite frontend as a child process, opens the
browser once both are reachable, and tears everything down when the server stops
(Stop button, all tabs closed, or Ctrl+C / closing the console window).
"""

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .config import ROOT, settings

log = logging.getLogger("mco.launcher")

HOST = "127.0.0.1"
BACKEND_PORT = settings.backend_port
FRONTEND_PORT = settings.frontend_port
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}"


PORT_TRIES = 20  # how many ports above the usual one are tried when it is taken


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def port_free(port: int) -> bool:
    """Nothing answers on the port and it can be bound (a program listening on all addresses blocks it too)."""
    if port_in_use(port):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def app_on(port: int) -> dict | None:
    """The health answer of THIS app on that port, or None when the port is free or another program has it."""
    if not port_in_use(port):
        return None
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=2) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    ours = isinstance(info, dict) and info.get("status") == "ok" and "profile" in info and "media_dir" in info
    return info if ours else None


def pick_ports(backend: int, frontend: int, profile: str, probe=app_on, free=port_free) -> tuple[str, int, int]:
    """Decide what a start does.

    ("running", backend_port, frontend_port): this app, with this profile, already runs there.
    ("start", backend_port, frontend_port): start on these ports. They are the usual ones, or the
    next free ones when another program (for example one that also likes port 8000) has taken them.
    """
    for i in range(PORT_TRIES):
        info = probe(backend + i)
        if info and info.get("profile") == profile:
            return "running", backend + i, int(info.get("frontend_port") or frontend + i)
    b = next((backend + i for i in range(PORT_TRIES) if free(backend + i)), None)
    f = next((frontend + i for i in range(PORT_TRIES) if free(frontend + i)), None)
    if b is None or f is None:
        raise RuntimeError(f"No free port found near {backend if b is None else frontend}")
    return "start", b, f


def wait_for_url(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def start_frontend(backend_port: int, frontend_port: int) -> subprocess.Popen:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        sys.exit("npm not found on PATH - install Node.js and run setup.bat")
    return subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(frontend_port), "--strictPort"],
        cwd=ROOT / "frontend",
        env={**os.environ, "MCO_BACKEND_PORT": str(backend_port), "MCO_FRONTEND_PORT": str(frontend_port)},
    )


def stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # npm spawns node as a grandchild; /T kills the whole tree.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Media Collection Organizer")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument("--no-auto-close", action="store_true",
                        help="keep running after all browser tabs close")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    try:
        what, backend_port, frontend_port = pick_ports(BACKEND_PORT, FRONTEND_PORT, settings.profile)
    except RuntimeError as e:
        sys.exit(f"[!] {e}. Close a program that uses these ports and start again.")
    frontend_url = f"http://localhost:{frontend_port}"
    if what == "running":
        log.info("The app is already running - opening %s in the browser instead.", frontend_url)
        if not args.no_browser:
            webbrowser.open(frontend_url)
        return
    # Another program has the usual port (many local apps and dev servers use 8000 too): move over.
    if backend_port != BACKEND_PORT:
        log.warning("Port %s is used by another program. This app uses port %s this time.", BACKEND_PORT, backend_port)
    if frontend_port != FRONTEND_PORT:
        log.warning("Port %s is used by another program. Open the app at %s this time.", FRONTEND_PORT, frontend_url)
    settings.backend_port, settings.frontend_port = backend_port, frontend_port
    settings.cors_origins = [f"http://localhost:{frontend_port}"]

    if args.no_auto_close:
        settings.auto_close = False

    from .main import app  # import after settings tweaks

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=backend_port, log_level="info"))

    def shutdown(reason: str) -> None:
        log.info("Shutting down: %s", reason)
        server.should_exit = True

    app.state.shutdown_callback = shutdown

    frontend = start_frontend(backend_port, frontend_port)

    def open_browser_when_ready() -> None:
        if wait_for_url(frontend_url, timeout=60):
            webbrowser.open(frontend_url)
        else:
            log.error("Frontend did not come up within 60s")

    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, daemon=True).start()

    try:
        server.run()
    finally:
        log.info("Stopping frontend...")
        stop_process_tree(frontend)
        log.info("Stopped. You can close this window.")


if __name__ == "__main__":
    main()
