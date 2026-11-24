import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.game.config import get_config

BACKEND = Path(__file__).resolve().parents[1]


def _run(tmp_path, *args, extra_env=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("MCO_DATA_DIR", "MCO_MEDIA_DIR", "MCO_DATABASE_URL", "MCO_PROFILE")}
    env["MCO_PROFILES_DIR"] = str(tmp_path)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "tools.testdata", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )


def test_testdata_refuses_main_profile(tmp_path):
    r = _run(tmp_path, "--profile", "main", "--images", "1")
    assert r.returncode == 2
    assert "Refusing to touch the main (player) profile." in (r.stdout + r.stderr)
    assert list(tmp_path.iterdir()) == []


def test_testdata_seeds_and_resets(tmp_path):
    r = _run(tmp_path, "--profile", "t1", "--images", "3")
    assert r.returncode == 0, r.stderr
    media = tmp_path / "t1" / "media"
    pngs = sorted(p.name for p in media.glob("img_*.png"))
    assert pngs == ["img_001.png", "img_002.png", "img_003.png"]
    assert len({(media / n).read_bytes() for n in pngs}) == 3
    (tmp_path / "t1" / "stale.txt").write_text("old")
    r = _run(tmp_path, "--profile", "t1", "--reset", "--images", "2")
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "t1" / "stale.txt").exists()
    assert len(list(media.glob("img_*.png"))) == 2


def _coins(db_file):
    con = sqlite3.connect(db_file)
    try:
        return con.execute("select coins from users").fetchone()[0]
    finally:
        con.close()


def test_testdata_credit_writes_profile_db(tmp_path):
    r = _run(tmp_path, "--profile", "t2", "--images", "1", "--credit", "1000")
    assert r.returncode == 0, r.stderr
    assert _coins(tmp_path / "t2" / "data" / "app.db") == get_config().starting_coins + 1000


def test_env_file_database_url_cannot_redirect_credit(tmp_path):
    decoy = tmp_path / "decoy.db"
    envfile = tmp_path / "evil.env"
    envfile.write_text(f"MCO_DATABASE_URL=sqlite:///{decoy.as_posix()}" + "\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    r = _run(profiles, "--profile", "t3", "--images", "1", "--credit", "500",
             extra_env={"MCO_ENV_FILE": str(envfile)})
    assert r.returncode == 0, r.stderr
    assert not decoy.exists()
    assert _coins(profiles / "t3" / "data" / "app.db") == get_config().starting_coins + 500


def test_invalid_profile_names_refused(tmp_path):
    for name in ("data", "media", "Bad", "../x", "a b"):
        r = _run(tmp_path, "--profile", name, "--images", "1")
        assert r.returncode == 2, name
    assert list(tmp_path.iterdir()) == []


def test_check_base_protects_player_folders(tmp_path):
    from tools.testdata import check_base

    root = tmp_path / "repo"
    for d in ("data", "media", "profiles"):
        (root / d).mkdir(parents=True)
    assert check_base(root / "profiles" / "test", root) is None
    assert check_base(tmp_path / "elsewhere" / "test", root) is None
    for bad in (root, root / "data", root / "media", root / "data" / "sub", tmp_path):
        assert check_base(bad, root), bad


def test_reset_refuses_folder_without_marker(tmp_path):
    victim = tmp_path / "t2"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep")
    r = _run(tmp_path, "--profile", "t2", "--reset", "--images", "1")
    assert r.returncode == 2
    assert "marker" in (r.stdout + r.stderr)
    assert (victim / "precious.txt").exists()


def test_asking_for_videos_without_samples_fails_loudly(tmp_path):
    r = _run(tmp_path, "--profile", "t9", "--images", "1", "--videos", "2")
    samples = BACKEND / "tools" / "sample_media"
    if samples.is_dir() and len(list(samples.glob("*.mp4"))) >= 2:
        assert r.returncode == 0
    else:
        assert r.returncode == 2 and "sample video" in r.stderr
