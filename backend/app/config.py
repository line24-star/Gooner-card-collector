import json
import os
import zlib
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

_version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
APP_STAGE: str = _version["stage"]
APP_VERSION: str = _version["version"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.environ.get("MCO_ENV_FILE") or ROOT / ".env", env_prefix="MCO_", extra="ignore")

    # Profiles keep dev/test data apart from the player's: "main" is the player's own.
    profile: str = Field(default="main", pattern=r"^[a-z][a-z0-9-]{0,31}$")
    profiles_dir: Path = ROOT / "profiles"
    backend_port: int = 8000
    frontend_port: int = 5173

    media_dir: Path = ROOT / "media"
    data_dir: Path = ROOT / "data"
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'app.db').as_posix()}"
    secret_key: str = "change-me"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Lifecycle: shut the server down once every browser tab has been closed.
    auto_close: bool = True
    auto_close_grace_seconds: float = 15.0

    # Server log files in <data_dir>/logs: one per day, deleted after this many days.
    log_to_file: bool = True
    log_days: int = Field(default=2, ge=1, le=30)

    # v0.3.0: the folder is scanned when the player asks (choosing a folder, the Scan button).
    # True brings back the old behaviour: scan at startup and watch the folder all the time.
    watch_media: bool = False

    @model_validator(mode="after")
    def _apply_profile(self) -> "Settings":
        explicit = self.model_fields_set
        if self.profile != "main":
            base = self.profiles_dir / self.profile
            # A non-main profile is always confined to its own folder: .env or environment
            # values for these three would otherwise point it at the player's media or DB.
            self.media_dir = base / "media"
            self.data_dir = base / "data"
            self.database_url = f"sqlite:///{(base / 'data' / 'app.db').as_posix()}"
            if self.profile == "test":
                n = 10
            else:
                n = 1 + zlib.crc32(self.profile.encode()) % 50
            if "backend_port" not in explicit:
                self.backend_port = 8000 + 10 * n
            if "frontend_port" not in explicit:
                self.frontend_port = 5173 + 10 * n
        if "cors_origins" not in explicit:
            self.cors_origins = [f"http://localhost:{self.frontend_port}"]
        return self


settings = Settings()
