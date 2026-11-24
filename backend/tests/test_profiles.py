from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ROOT, Settings

_PROFILE_VARS = (
    "MCO_PROFILE", "MCO_MEDIA_DIR", "MCO_DATA_DIR", "MCO_DATABASE_URL",
    "MCO_BACKEND_PORT", "MCO_FRONTEND_PORT", "MCO_PROFILES_DIR", "MCO_CORS_ORIGINS",
)


def _clean(monkeypatch):
    for v in _PROFILE_VARS:
        monkeypatch.delenv(v, raising=False)


def test_main_profile_keeps_default_paths_and_ports(monkeypatch):
    _clean(monkeypatch)
    s = Settings(_env_file=None)
    assert s.profile == "main"
    assert s.media_dir == ROOT / "media"
    assert s.data_dir == ROOT / "data"
    assert s.database_url == f"sqlite:///{(ROOT / 'data' / 'app.db').as_posix()}"
    assert (s.backend_port, s.frontend_port) == (8000, 5173)
    assert "http://localhost:5173" in s.cors_origins


def test_profile_paths_and_ports_are_isolated(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("MCO_PROFILE", "test")
    s = Settings(_env_file=None)
    base = ROOT / "profiles" / "test"
    assert s.media_dir == base / "media"
    assert s.data_dir == base / "data"
    assert s.database_url == f"sqlite:///{(base / 'data' / 'app.db').as_posix()}"
    assert (s.backend_port, s.frontend_port) == (8100, 5273)
    assert "http://localhost:5273" in s.cors_origins


def test_other_profile_gets_stable_distinct_ports(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("MCO_PROFILE", "demo")
    a = Settings(_env_file=None)
    b = Settings(_env_file=None)
    assert (a.backend_port, a.frontend_port) == (b.backend_port, b.frontend_port)
    n = (a.backend_port - 8000) // 10
    assert 1 <= n <= 50 and a.backend_port == 8000 + 10 * n and a.frontend_port == 5173 + 10 * n


def test_explicit_env_overrides_ports_but_not_locations(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("MCO_PROFILE", "test")
    monkeypatch.setenv("MCO_MEDIA_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("MCO_BACKEND_PORT", "9999")
    s = Settings(_env_file=None)
    assert s.backend_port == 9999
    assert s.media_dir == ROOT / "profiles" / "test" / "media"  # locations are forced


def _env_file(tmp_path):
    f = tmp_path / "player.env"
    f.write_text(
        f"MCO_MEDIA_DIR={tmp_path / 'playermedia'}\n"
        f"MCO_DATA_DIR={tmp_path / 'playerdata'}\n"
        f"MCO_DATABASE_URL=sqlite:///{(tmp_path / 'player.db').as_posix()}\n",
        encoding="utf-8",
    )
    return f


def test_non_main_profile_ignores_env_file_locations(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("MCO_PROFILE", "test")
    s = Settings(_env_file=_env_file(tmp_path))
    base = ROOT / "profiles" / "test"
    assert s.media_dir == base / "media"
    assert s.data_dir == base / "data"
    assert s.database_url == f"sqlite:///{(base / 'data' / 'app.db').as_posix()}"


def test_main_profile_still_honors_env_file_locations(monkeypatch, tmp_path):
    _clean(monkeypatch)
    s = Settings(_env_file=_env_file(tmp_path))
    assert s.media_dir == tmp_path / "playermedia"
    assert s.data_dir == tmp_path / "playerdata"
    assert s.database_url.endswith("player.db")


def test_profiles_dir_is_overridable(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("MCO_PROFILE", "test")
    monkeypatch.setenv("MCO_PROFILES_DIR", str(tmp_path))
    s = Settings(_env_file=None)
    assert s.media_dir == tmp_path / "test" / "media"


def test_invalid_profile_name_rejected(monkeypatch):
    import pytest

    _clean(monkeypatch)
    monkeypatch.setenv("MCO_PROFILE", "../evil")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_health_reports_profile():
    from app.main import app

    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["profile"] == "main"
