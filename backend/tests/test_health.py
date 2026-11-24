import json
import re

from fastapi.testclient import TestClient

from app.config import ROOT
from app.main import app

VERSION = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))


def test_health():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["version"] == VERSION["version"]
    assert r.json()["stage"] == VERSION["stage"]


def test_version_format_and_sync():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION["version"])
    pyproject = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{VERSION["version"]}"' in pyproject, "bump backend/pyproject.toml too"
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == VERSION["version"], "bump frontend/package.json too"
