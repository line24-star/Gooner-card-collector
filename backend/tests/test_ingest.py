"""Ingestion of a single media path."""

import shutil
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.game.config import get_config
from app.game.media import classify
from app.models import CollectionEntry, MediaItem, Tag
from app.services.ingest import ingest_path, mark_missing
from app.services.wallet import local_user

cfg = get_config()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def root(tmp_path, monkeypatch):
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    return d


def make_png(p: Path, color="red"):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), color).save(p)
    return p


def rows(db):
    return db.scalars(select(MediaItem)).all()


def test_classify():
    assert classify(Path("a.JPG")) == "image" and classify(Path("b.mkv")) == "video" and classify(Path("c.txt")) is None


def test_ingest_new_image_records_metadata(db, root):
    u = local_user(db)
    p = make_png(root / "x.png")
    r = ingest_path(db, u, p, initial_scan=False)
    assert r.status == "new"
    row = db.get(MediaItem, r.media_id)
    assert (row.width, row.height, row.format, row.path, row.kind) == (40, 30, "PNG", "x.png", "image")
    assert row.size_bytes == p.stat().st_size
    assert r.coins == cfg.ingest.image


def test_ingest_initial_scan_pays_nothing(db, root):
    u = local_user(db)
    p = make_png(root / "x.png")
    r = ingest_path(db, u, p, initial_scan=True)
    assert r.status == "new" and r.coins == 0 and len(rows(db)) == 1


def test_ingest_unchanged_second_time(db, root):
    u = local_user(db)
    p = make_png(root / "x.png")
    ingest_path(db, u, p, initial_scan=False)
    r = ingest_path(db, u, p, initial_scan=False)
    assert r.status == "unchanged" and r.coins == 0


def test_ingest_duplicate_content_other_name(db, root):
    u = local_user(db)
    a = make_png(root / "a.png")
    ingest_path(db, u, a, initial_scan=False)
    b = root / "b.png"
    shutil.copyfile(a, b)
    r = ingest_path(db, u, b, initial_scan=False)
    assert r.status == "duplicate" and r.coins == 0
    assert len(rows(db)) == 1


def test_ingest_moved_keeps_tags_and_ownership(db, root):
    u = local_user(db)
    a = make_png(root / "a.png")
    r = ingest_path(db, u, a, initial_scan=False)
    row = db.get(MediaItem, r.media_id)
    row.tags.append(Tag(name="cat"))
    db.add(CollectionEntry(user_id=u.id, media_id=row.id, treatment="normal", copies=1))
    db.commit()
    (root / "sub").mkdir()
    b = root / "sub" / "b.png"
    a.rename(b)
    r2 = ingest_path(db, u, b, initial_scan=False)
    assert r2.status == "moved" and r2.media_id == r.media_id
    db.expire_all()
    row = db.get(MediaItem, r.media_id)
    assert row.path == "sub/b.png"
    assert [t.name for t in row.tags] == ["cat"]
    assert db.scalar(select(CollectionEntry).where(CollectionEntry.media_id == row.id)).copies == 1


def test_ingest_restored_after_missing(db, root):
    u = local_user(db)
    a = make_png(root / "a.png")
    r = ingest_path(db, u, a, initial_scan=False)
    mark_missing(db, set())
    assert db.get(MediaItem, r.media_id).missing is True
    r2 = ingest_path(db, u, a, initial_scan=False)
    assert r2.status == "restored" and r2.coins == 0
    db.expire_all()
    assert db.get(MediaItem, r.media_id).missing is False


def test_ingest_edited_file_replaces_old_card(db, root):
    u = local_user(db)
    a = make_png(root / "a.png", "red")
    r = ingest_path(db, u, a, initial_scan=False)
    make_png(a, "blue")
    r2 = ingest_path(db, u, a, initial_scan=False)
    assert r2.status == "new" and r2.media_id != r.media_id
    db.expire_all()
    assert db.get(MediaItem, r.media_id).missing is True
    assert db.get(MediaItem, r2.media_id).missing is False


def test_ingest_skips_zero_byte_corrupt_and_unsupported(db, root):
    u = local_user(db)
    (root / "empty.png").write_bytes(b"")
    (root / "corrupt.jpg").write_bytes(b"not an image")
    (root / "note.txt").write_text("hi")
    for n in ("empty.png", "corrupt.jpg", "note.txt"):
        r = ingest_path(db, u, root / n, initial_scan=False)
        assert r.status == "skipped" and r.coins == 0 and r.reason
    assert rows(db) == []


def test_ingest_skips_unreadable(db, root):
    u = local_user(db)
    r = ingest_path(db, u, root / "gone.png", initial_scan=False)
    assert r.status == "skipped"


def test_ingest_video_records_basics(db, root):
    u = local_user(db)
    p = root / "v.MP4"
    p.write_bytes(b"fake video bytes")
    r = ingest_path(db, u, p, initial_scan=False)
    row = db.get(MediaItem, r.media_id)
    assert r.status == "new" and row.kind == "video" and row.format == "mp4" and row.width is None


def test_ingest_unicode_filename(db, root):
    u = local_user(db)
    p = make_png(root / "été 📷.png")
    r = ingest_path(db, u, p, initial_scan=False)
    assert r.status == "new"
    assert db.get(MediaItem, r.media_id).path == "été 📷.png"


def test_mark_missing_counts(db, root):
    u = local_user(db)
    ingest_path(db, u, make_png(root / "a.png", "red"), initial_scan=True)
    ingest_path(db, u, make_png(root / "b.png", "blue"), initial_scan=True)
    assert mark_missing(db, {"a.png"}) == 1
    assert mark_missing(db, {"a.png"}) == 0


def test_restore_after_edit_revert_leaves_one_present_row(db, root):
    u = local_user(db)
    a = make_png(root / "a.png", "red")
    r1 = ingest_path(db, u, a, initial_scan=False)
    make_png(a, "blue")
    r2 = ingest_path(db, u, a, initial_scan=False)
    make_png(a, "red")
    r3 = ingest_path(db, u, a, initial_scan=False)
    assert r3.status == "restored" and r3.media_id == r1.media_id
    db.expire_all()
    present = db.scalars(select(MediaItem).where(MediaItem.path == "a.png", MediaItem.missing.is_(False))).all()
    assert [m.id for m in present] == [r1.media_id]
    assert db.get(MediaItem, r2.media_id).missing is True


def test_moved_onto_occupied_path_retires_other_row(db, root):
    u = local_user(db)
    a = make_png(root / "a.png", "red")
    ra = ingest_path(db, u, a, initial_scan=False)
    b = make_png(root / "b.png", "blue")
    rb = ingest_path(db, u, b, initial_scan=False)
    # a.png's content moves to b.png, replacing b's content
    a.replace(b)
    r = ingest_path(db, u, b, initial_scan=False)
    assert r.status == "moved" and r.media_id == ra.media_id
    db.expire_all()
    assert db.get(MediaItem, ra.media_id).missing is False
    assert db.get(MediaItem, rb.media_id).missing is True


def test_db_error_propagates(db, root, monkeypatch):
    from sqlalchemy.exc import OperationalError

    u = local_user(db)
    p = make_png(root / "x.png")

    def boom(*a, **k):
        raise OperationalError("select", {}, Exception("database is locked"))

    monkeypatch.setattr(db, "scalars", boom)
    with pytest.raises(OperationalError):
        ingest_path(db, u, p, initial_scan=False)


def test_case_only_rename_on_case_insensitive_fs_is_moved(db, root, monkeypatch):
    """Simulates a case-insensitive filesystem on any OS: the old spelling still 'exists' and is the same file."""
    import os

    from app.services import ingest as ingest_mod

    user = local_user(db)
    make_png(root / "a.png")
    assert ingest_path(db, user, root / "a.png", initial_scan=True).status == "new"
    os.rename(root / "a.png", root / "A.png")
    real_exists, real_samefile = Path.exists, os.path.samefile

    def exists(self, *a, **k):
        return real_exists(self, *a, **k) or (self.name == "a.png" and real_exists(self.with_name("A.png")))

    def samefile(x, y):
        norm = lambda p: str(p).replace("a.png", "A.png")  # noqa: E731
        return real_samefile(norm(x), norm(y))

    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(ingest_mod.os.path, "samefile", samefile)
    assert ingest_path(db, user, root / "A.png", initial_scan=False).status == "moved"
    assert mark_missing(db, {"A.png"}) == 0
    row = rows(db)[0]
    assert (row.path, row.missing) == ("A.png", False)
