"""Card names: readable titles from file names, generated names for machine-made ones, custom titles."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.db import SCHEMA_VERSION, SessionLocal, migrate
from app.game import titles
from app.main import app
from app.models import MediaItem, Tag
from app.services import cards, organize
from app.services.wallet import local_user
from tests.test_chase import own


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


def add(db, name, **kw):
    m = MediaItem(path=name, sha256=hashlib.sha256(name.encode()).hexdigest(), kind="image", **kw)
    db.add(m)
    db.commit()
    return m


def tag(db, m, *names):
    for n in names:
        t = Tag(name=n)
        db.add(t)
        m.tags.append(t)
    db.commit()


# ---------------------------------------------------------------- pure rules

GIBBERISH = [
    # machine-made media ids
    "GKtwUBnXcAAQeLp",
    "GT4m8LPWIAAkddx",
    "GZDMAuoWYA7R_7_",
    "GKtwUBnXcAAQeLp (1)",
    "example.com-GKtwUBnXcAAQeLp",
    "GKtw-UBnXcAAQeLp",  # an id cut up by its own separator
    # cameras and apps
    "IMG_1234",
    "IMG-20240101-WA0012",
    "DSC01234",
    "DSCN0042",
    "PXL_20260101_123456789",
    "PXL_20260101_123456789.MP",
    "MVI_0012",
    "VID_20250101_101010",
    "GOPR0123",
    "Screenshot 2026-01-01 at 10.30.15",
    "Screenshot_20260101-123456",
    "Screenshot (12)",
    "Screen Shot 2026-01-01 at 10.30.15 AM",
    "photo_2026-01-01_12-00-00",
    "WhatsApp Image 2026-01-01 at 10.30.15",
    "FB_IMG_1700000000000",
    "signal-2026-01-01-10-30-15-123",
    "image (3)",
    "Untitled",
    "download (2)",
    # ids, hashes, counters
    "550e8400-e29b-41d4-a716-446655440000",
    "d41d8cd98f00b204e9800998ecf8427e",
    "deadbeefdeadbeef",
    "1700000000000",
    "20260101_123456",
    "12345",
    "abc123xyz",
    "x1234567",
    "v0-abc123xyz456",  # an id suffix with no slug
    "bcdfghjklm",  # no vowels
    "WXZQRTPLKD",
    "",
    "___",
]

MEANINGFUL = [
    # the conservative cases from the brief
    "sunset",
    "Holiday2019",
    "my-cat",
    # long slugs are words, not gibberish
    "whats-better-than-one-sunny-day-v0-abc123xyz",
    "example.com-maria_Lopez_GKtwUBnXcAAQeLp",
    "wobbly (1)",
    # ordinary names
    "holo pic",
    "Beach",
    "birthday_party_2019",
    "2019Holiday",
    "sunset20190812",
    "MyCatPhoto",
    "HolidayInSpain",
    "iPhoneWallpaper",
    "McDonalds",
    "NYCTrip",
    "SUNSETBEACH",
    "strengths",
    "catchphrase",
    "wallpaper",
    "c0",  # short names are never called gibberish
    "a",
    "pic",
    "Screenshot of my cat",
    "image of a sunset",
    "cat-abc123xyz",  # one real word is enough
    "日本語のファイル",
    "Fotografía",
]


@pytest.mark.parametrize("stem", GIBBERISH)
def test_machine_made_names_are_gibberish(stem):
    assert titles.is_gibberish(stem) is True


@pytest.mark.parametrize("stem", MEANINGFUL)
def test_readable_names_are_not_gibberish(stem):
    assert titles.is_gibberish(stem) is False


@pytest.mark.parametrize(("stem", "expected"), [
    ("sunset", "Sunset"),
    ("my-cat", "My cat"),
    ("Holiday2019", "Holiday2019"),
    ("holo pic", "Holo pic"),
    ("wobbly (1)", "Wobbly"),
    ("wobbly (1) (2)", "Wobbly"),
    ("wobbly - Copy", "Wobbly"),
    ("wobbly - Copy (2)", "Wobbly"),
    ("whats-better-than-one-sunny-day-v0-abc123xyz", "Whats better than one sunny day"),
    ("example.com-maria_Lopez_GKtwUBnXcAAQeLp", "Maria Lopez"),
    ("example.com-maria_Lopez_GZDMAuoWYA7R_7_", "Maria Lopez"),
    ("www.example.org_beach.day", "Beach day"),
    ("birthday_party_2019", "Birthday party 2019"),  # a year stays
    ("cat-abc123xyz", "Cat"),
    ("  some   spaced__name..here  ", "Some spaced name here"),
    ("Mom's birthday (final)", "Mom's birthday (final)"),
    ("élan-vital", "Élan vital"),
    # 40 characters at most, cut between words
    ("the quick brown fox jumps over the lazy dog again", "The quick brown fox jumps over the lazy"),
    ("a-very-long-name-that-ends-exactly-at-the-forty-mark", "A very long name that ends exactly at"),
    ("x" * 50, "X" + "x" * 39),
    ("___", ""),
])
def test_clean_filename(stem, expected):
    out = titles.clean_filename(stem)
    assert out == expected
    assert len(out) <= titles.MAX_CLEAN_LEN


def test_display_title_order():
    # the player's own title wins over everything
    assert titles.display_title("GKtwUBnXcAAQeLp", "My favourite", 412) == "My favourite"
    assert titles.display_title("sunset", "  Dusk  ", 1) == "Dusk"
    # v0.9.0: a file name no longer makes a name, however readable it is
    assert titles.display_title("my-cat", None, 412) == "Card #000412"
    assert titles.display_title("my-cat", "   ", 412) == "Card #000412"
    # every card without a name of its own: "Card" and its six-digit number (v0.8.0: tags make no names either)
    assert titles.display_title("GKtwUBnXcAAQeLp", None, 412) == "Card #000412"
    assert titles.display_title("GZDMAuoWYA7R_7_", None, 7) == "Card #000007"
    assert titles.display_title("IMG_1234", "", 1234567) == "Card #1234567"
    assert titles.card_code(123) == "#000123" and titles.generated_title(123) == "Card #000123"


def test_normalize_title():
    assert titles.normalize_title("  My   best  card ") == "My best card"
    assert titles.normalize_title(None) is None
    assert titles.normalize_title("") is None
    assert titles.normalize_title("   ") is None
    assert titles.normalize_title("x" * 60) == "x" * 60
    assert titles.normalize_title("Ünïcödé 🌅") == "Ünïcödé 🌅"
    for bad in ("x" * 61, "line\nbreak", "tab\there", "nul\x00", "bell\x07", "esc\x1b[0m"):
        with pytest.raises(ValueError):
            titles.normalize_title(bad)


# ---------------------------------------------------------------- database


def test_migration_adds_the_title_column_and_keeps_cards(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'v5.db').as_posix()}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, "
                       "password_hash VARCHAR(255), coins INTEGER, packs_opened INTEGER NOT NULL DEFAULT 0, "
                       "streak_days INTEGER NOT NULL DEFAULT 0, last_active_day VARCHAR(10), created_at DATETIME)"))
        c.execute(text("CREATE TABLE media_items (id INTEGER PRIMARY KEY, path TEXT, sha256 VARCHAR(64) UNIQUE, "
                       "kind VARCHAR(8), treatment VARCHAR(16), ingested_at DATETIME, "
                       "size_bytes INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER, format VARCHAR(16), "
                       "duration_s REAL, mtime REAL NOT NULL DEFAULT 0, missing INTEGER NOT NULL DEFAULT 0, "
                       "print_run INTEGER NOT NULL DEFAULT 0, popularity REAL NOT NULL DEFAULT 1.0)"))
        c.execute(text("INSERT INTO media_items (path, sha256, kind, treatment) VALUES ('a.jpg', 'abc', 'image', 'normal')"))
        c.execute(text("PRAGMA user_version = 5"))
    assert migrate(eng) == SCHEMA_VERSION
    assert "title" in {c["name"] for c in inspect(eng).get_columns("media_items")}
    with eng.begin() as c:
        assert tuple(c.execute(text("SELECT path, title FROM media_items")).one()) == ("a.jpg", None)
    assert migrate(eng) == SCHEMA_VERSION  # running it again changes nothing


# ---------------------------------------------------------------- the card payload


def test_card_payload_names(db):
    u = local_user(db)
    readable = add(db, "sub/folder/my-cat (1).jpg")
    machine = add(db, "GKtwUBnXcAAQeLp.jpg")
    tagged = add(db, "GT4m8LPWIAAkddx.jpg")
    tag(db, tagged, "shiny", "lake")
    out = {c["id"]: c for c in cards.card_out_many(db, u, [readable, machine, tagged])}

    assert out[readable.id]["title"] == f"Card #{readable.number:06d}"  # v0.9.0: not "My cat"
    assert out[readable.id]["file_name"] == "my-cat (1)" and out[readable.id]["title_custom"] is False
    assert out[machine.id]["title"] == f"Card #{machine.number:06d}"
    assert out[machine.id]["file_name"] == "GKtwUBnXcAAQeLp" and out[machine.id]["title_custom"] is False
    assert out[tagged.id]["title"] == f"Card #{tagged.number:06d}"  # v0.8.0: tags no longer make names

    machine.title = "The red one"
    db.commit()
    c = cards.card_out(db, u, machine)
    assert c["title"] == "The red one" and c["title_custom"] is True and c["file_name"] == "GKtwUBnXcAAQeLp"


def test_a_tag_does_not_change_a_generated_name(db):
    u = local_user(db)
    m = add(db, "GZDMAuoWYA7R_7_.jpg")
    own(db, u, m, "normal")
    name = f"Card #{m.number:06d}"
    assert cards.card_out(db, u, m)["title"] == name
    assert organize.add_tag(db, u, m.id, "Sunset")["card"]["title"] == name
    assert organize.add_tag(db, u, m.id, "beach")["card"]["title"] == name


# ---------------------------------------------------------------- PATCH /api/media/{id}


def test_rename_sets_trims_and_clears(client):
    with SessionLocal() as db:
        m = add(db, "GKtwUBnXcAAQeLp.jpg")
        own(db, local_user(db), m, "normal")
        mid = m.id
    r = client.patch(f"/api/media/{mid}", json={"title": "  My   red card  "})
    assert r.status_code == 200
    card = r.json()
    assert card["id"] == mid and card["title"] == "My red card" and card["title_custom"] is True
    assert card["file_name"] == "GKtwUBnXcAAQeLp"
    assert client.get(f"/api/media/{mid}").json()["title"] == "My red card"
    with SessionLocal() as db:
        assert db.get(MediaItem, mid).title == "My red card"
        assert db.get(MediaItem, mid).path == "GKtwUBnXcAAQeLp.jpg"  # the file keeps its name

    for clear in ("", "   ", None):
        client.patch(f"/api/media/{mid}", json={"title": "Again"})
        r = client.patch(f"/api/media/{mid}", json={"title": clear})
        assert r.status_code == 200
        assert r.json()["title"] == f"Card {r.json()['code']}" and r.json()["title_custom"] is False
        with SessionLocal() as db:
            assert db.get(MediaItem, mid).title is None

    assert client.patch(f"/api/media/{mid}", json={"title": "x" * 60}).json()["title"] == "x" * 60


def test_rename_rejects_bad_input(client):
    with SessionLocal() as db:
        u = local_user(db)
        mine = add(db, "mine.jpg")
        own(db, u, mine, "normal")
        other = add(db, "not-pulled.jpg")
        gone = add(db, "gone.jpg", missing=True)
        sold = add(db, "sold.jpg")
        own(db, u, sold, "normal", copies=0)
        mine_id, other_id, gone_id, sold_id = mine.id, other.id, gone.id, sold.id

    for bad in ("x" * 61, "line\nbreak", "tab\there", "nul\x00char"):
        assert client.patch(f"/api/media/{mine_id}", json={"title": bad}).status_code == 422
    assert client.patch(f"/api/media/{mine_id}", json={}).status_code == 422  # the title field is required
    assert client.patch(f"/api/media/{mine_id}", json={"title": 5}).status_code == 422
    assert client.patch("/api/media/999999", json={"title": "x"}).status_code == 404
    assert client.patch(f"/api/media/{gone_id}", json={"title": "x"}).status_code == 404
    assert client.patch(f"/api/media/{other_id}", json={"title": "x"}).status_code == 409  # not pulled yet
    assert client.patch(f"/api/media/{sold_id}", json={"title": "x"}).status_code == 409  # no copies left
    with SessionLocal() as db:
        assert [m.title for m in db.query(MediaItem).order_by(MediaItem.id)] == [None] * 4


def test_rename_needs_the_client_header():
    with SessionLocal() as db:
        m = add(db, "mine.jpg")
        own(db, local_user(db), m, "normal")
        mid = m.id
    with TestClient(app) as bare:
        assert bare.patch(f"/api/media/{mid}", json={"title": "x"}).status_code == 403


# ---------------------------------------------------------------- search and order


def test_list_sorts_and_searches_by_the_shown_name(client):
    with SessionLocal() as db:
        u = local_user(db)
        zebra = add(db, "zebra.jpg")  # "Card #00000n" (v0.9.0: a readable file name makes no name either)
        machine = add(db, "GKtwUBnXcAAQeLp.jpg")  # "Card #00000n"
        apple = add(db, "GT4m8LPWIAAkddx.jpg")  # renamed below
        tagged = add(db, "IMG_1234.jpg")  # "Card #00000n" too: its tag makes no name
        tag(db, tagged, "mountain")
        own(db, u, apple, "normal")
        ids = {"zebra": zebra.id, "machine": machine.id, "apple": apple.id, "tagged": tagged.id}
    client.patch(f"/api/media/{ids['apple']}", json={"title": "apple pie"})

    items = client.get("/api/media?sort=name").json()["items"]
    # the named card first, then the numbered ones in the order of their numbers
    assert [i["id"] for i in items] == [ids["apple"], ids["zebra"], ids["machine"], ids["tagged"]]
    names = [i["title"] for i in items]
    assert names == sorted(names, key=str.casefold)

    def found(q):
        return {i["id"] for i in client.get("/api/media", params={"q": q}).json()["items"]}

    assert found("APPLE") == {ids["apple"]}  # the custom name
    assert found("gt4m8") == {ids["apple"]}  # the real file name still finds it
    assert found("mountain") == set()  # a tag is not part of the name (the tag filter finds it)
    assert found("card #") == {ids["zebra"], ids["machine"], ids["tagged"]}  # generated names
    code = next(i["code"] for i in items if i["id"] == ids["zebra"])
    assert found(code) == {ids["zebra"]}  # the number finds a card whatever its name is
    assert found("zeb") == {ids["zebra"]}  # the real file name finds it, though it is not the name


# ---------------------------------------------------------------- reset


def test_reset_keeps_custom_titles(client):
    """A custom name describes the file, not the player's progress: a reset leaves it alone."""
    with SessionLocal() as db:
        m = add(db, "GKtwUBnXcAAQeLp.jpg")
        own(db, local_user(db), m, "normal")
        mid = m.id
    assert client.patch(f"/api/media/{mid}", json={"title": "Keeper"}).status_code == 200
    assert client.post("/api/account/reset", json={"confirm": "RESET"}).status_code == 200
    with SessionLocal() as db:
        assert db.get(MediaItem, mid).title == "Keeper"
    card = client.get(f"/api/media/{mid}").json()
    assert card["title"] == "Keeper" and card["title_custom"] is True and card["copies"] == 0
