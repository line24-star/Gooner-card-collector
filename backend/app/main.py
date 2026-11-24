import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import logs
from .config import APP_STAGE, APP_VERSION, settings
from .db import SessionLocal, init_db
from .lifecycle import PresenceMonitor, ShutdownCallback, default_shutdown
from .routers import account, economy, market as market_router, media, meta, onboarding, settings as settings_router, tags, tutorials
from .services import folders, scanner, wallet

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
# Host header allow-list (blocks DNS rebinding). "testserver" is Starlette's TestClient.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
# Every mutating /api request must carry this header. A custom header can't be sent
# cross-site without a CORS preflight, so drive-by "simple" POSTs from other sites fail.
CLIENT_HEADER = "x-mco-client"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# /api/shutdown already requires its own custom header (X-MCO-Action), which blocks the same attacks.
CLIENT_HEADER_EXEMPT = {"/api/shutdown"}


def _request_shutdown(app: FastAPI, reason: str) -> None:
    callback: ShutdownCallback = getattr(app.state, "shutdown_callback", None) or default_shutdown
    callback(reason)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.log_to_file:
        logs.setup()
    init_db()
    with SessionLocal() as db:
        folders.load_override(db)
        wallet.local_user(db)  # the player exists before the first request (two at once used to race)
        wallet.top_up_starting_coins(db)  # players who started with fewer welcome coins (v0.1.1)
    folders.current_media_dir().mkdir(parents=True, exist_ok=True)
    app.state.presence = PresenceMonitor(
        on_idle=lambda reason: _request_shutdown(app, reason),
        grace_seconds=settings.auto_close_grace_seconds,
        enabled=settings.auto_close,
    )
    if settings.watch_media:
        scanner.start_background(folders.current_media_dir())
    try:
        yield
    finally:
        scanner.stop_background()  # a scan the player started may still be running


app = FastAPI(title="Media Collection Organizer", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_client_header(request: Request, call_next):
    path = request.url.path
    if (
        request.method not in SAFE_METHODS
        and path.startswith("/api")
        and path not in CLIENT_HEADER_EXEMPT
        and not request.headers.get(CLIENT_HEADER)
    ):
        return JSONResponse({"detail": "Missing X-MCO-Client header"}, status_code=403)
    return await call_next(request)


# Added last = outermost: bad hosts are rejected before anything else runs.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.include_router(tutorials.router)
app.include_router(meta.router)
app.include_router(economy.router)
app.include_router(onboarding.router)
app.include_router(media.router)
app.include_router(tags.router)
app.include_router(settings_router.router)
app.include_router(account.router)
app.include_router(market_router.router)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "stage": APP_STAGE,
        "media_dir": str(folders.current_media_dir()),
        "profile": settings.profile,
        # where the page is served: a second start of the app opens this instead of starting again
        "frontend_port": settings.frontend_port,
    }


class ClientLog(BaseModel):
    level: Literal["error", "warn", "info"] = "error"
    message: str = Field(max_length=4000)
    where: str = Field(default="", max_length=300)


_client_log = logging.getLogger("mco.client")
_client_log_window: list[float] = []  # times of recent entries: at most 60 a minute are written


@app.post("/api/log", status_code=204)
def client_log(entry: ClientLog) -> Response:
    """The browser reports its own errors here so they land in the server log file."""
    now = time.monotonic()
    _client_log_window[:] = [t for t in _client_log_window if now - t < 60]
    if len(_client_log_window) < 60:
        _client_log_window.append(now)
        level = {"error": logging.ERROR, "warn": logging.WARNING, "info": logging.INFO}[entry.level]
        _client_log.log(level, "%s | %s", " ".join(entry.where.split()), entry.message.strip())
    return Response(status_code=204)


@app.post("/api/shutdown")
def shutdown(request: Request, x_mco_action: str | None = Header(default=None)) -> dict:
    # Local-only, and the custom header blocks drive-by cross-site form posts.
    if request.client is None or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(403, "Shutdown is only allowed from this machine")
    if x_mco_action != "shutdown":
        raise HTTPException(400, "Missing X-MCO-Action: shutdown header")
    _request_shutdown(request.app, "stop requested from the app")
    return {"status": "shutting_down"}


@app.websocket("/api/ws/presence")
async def presence(ws: WebSocket) -> None:
    await ws.accept()
    monitor: PresenceMonitor = ws.app.state.presence
    monitor.connect()
    try:
        while True:
            await ws.receive_text()  # client pings; we just keep the socket alive
    except WebSocketDisconnect:
        pass
    finally:
        monitor.disconnect()
