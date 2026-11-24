"""Thumbnail cache."""

import os

import pytest
from PIL import Image

from app.config import settings
from app.db import SessionLocal
from app.services.ingest import ingest_path
from app.services.thumbs import THUMB_MAX, ensure_thumb, thumb_path
from app.services.wallet import local_user


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(settings, "media_dir", media)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    return media


def test_thumb_created_and_bounded(dirs):
    src = dirs / "big.png"
    Image.new("RGB", (2000, 1000), "blue").save(src)
    out = ensure_thumb(src, "ab" + "0" * 62)
    assert out is not None and out.exists()
    assert out == thumb_path("ab" + "0" * 62)
    with Image.open(out) as im:
        assert max(im.size) == THUMB_MAX
        assert im.format == "WEBP"


def test_thumb_cached_not_rewritten(dirs):
    src = dirs / "a.png"
    Image.new("RGB", (800, 600), "red").save(src)
    sha = "cd" + "1" * 62
    out = ensure_thumb(src, sha)
    os.utime(out, (1000, 1000))
    again = ensure_thumb(src, sha)
    assert again == out
    assert out.stat().st_mtime == 1000


def test_thumb_none_for_corrupt(dirs):
    src = dirs / "bad.png"
    src.write_bytes(b"not an image")
    assert ensure_thumb(src, "ef" + "2" * 62) is None
    assert ensure_thumb(dirs / "missing.png", "ef" + "3" * 62) is None


def test_thumb_never_written_in_media_dir(dirs):
    src = dirs / "p.png"
    Image.new("RGB", (900, 900), "green").save(src)
    before = sorted(dirs.rglob("*"))
    db = SessionLocal()
    try:
        res = ingest_path(db, local_user(db), src, initial_scan=True)
    finally:
        db.close()
    assert res.status == "new"
    assert sorted(dirs.rglob("*")) == before
    # v0.3.0: the import makes no thumbnail (that was most of its time); the first request does.
    assert not list((settings.data_dir / "thumbs").rglob("*.webp"))
    from app.services.thumbs import ensure_thumb
    import hashlib

    assert ensure_thumb(src, hashlib.sha256(src.read_bytes()).hexdigest()) is not None
    assert sorted(dirs.rglob("*")) == before
    assert list((settings.data_dir / "thumbs").rglob("*.webp"))


def test_thumb_none_on_unexpected_pillow_error_and_ingest_survives(dirs, monkeypatch):
    import struct

    from PIL import ImageOps

    def boom(*a, **k):
        raise struct.error("bad exif")

    monkeypatch.setattr(ImageOps, "exif_transpose", boom)
    src = dirs / "x.png"
    Image.new("RGB", (100, 100), "red").save(src)
    assert ensure_thumb(src, "aa" + "4" * 62) is None
    assert not list((settings.data_dir / "thumbs").rglob("*.tmp"))
    db = SessionLocal()
    try:
        res = ingest_path(db, local_user(db), src, initial_scan=True)
    finally:
        db.close()
    assert res.status == "new"


def test_thumb_path_names_its_size_so_old_smaller_thumbs_regenerate():
    p = thumb_path("ef" + "2" * 62)
    assert p.name == f"{'ef' + '2' * 62}-{THUMB_MAX}.webp"
    assert THUMB_MAX >= 720  # full-art cards: md/lg faces need about 720 px at 2x DPR
