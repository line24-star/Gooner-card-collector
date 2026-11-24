"""Card names: a readable title from a file name, or a generated one when the name is machine-made.

Pure rules, no dictionary. A card's title is the player's own title, else "Card" and the card's
number ("Card #000412"). v0.9.0: the file name no longer makes the name (every card without a
name of its own is a numbered card). The number is given once, when the card is scanned, and
never changes (v0.8.0). Tags no longer make names. Files on disk are never renamed.
"""

import re
import unicodedata
from functools import lru_cache

MAX_CLEAN_LEN = 40  # a cleaned file name is cut here, on a word boundary
MAX_TITLE_LEN = 60  # the longest custom title the player may set
MIN_MACHINE_LEN = 8  # shorter tokens are never called gibberish on their own

_VOWELS = frozenset("aeiouy")
# short words that may stand alone in a name ("my-cat", "iPhone", "DayAtTheZoo")
_SHORT_WORDS = frozenset(
    "a i an as at be by do go he if in is it me my no of oh ok on or so to up us we the and for "
    "mc de la le di du el st dr mr ms tv dj vs".split()
)

# " (1)" from a second download, " - Copy" from Windows Explorer
_COUNTER = re.compile(r"(?:\s*\(\d+\)|\s*-\s*copy(?:\s*\(\d+\))?)$", re.IGNORECASE)
# a slug with a version and an id behind it: "<slug>-v0-<id>"
_SLUG_ID = re.compile(r"(?:^|[-_ ])v\d[-_ ][a-z0-9]{6,}$", re.IGNORECASE)
# "example.com-...", "www.example.org_..."
_SITE = re.compile(
    r"^(?:www\.)?[a-z0-9]+(?:[.-][a-z0-9]+)*\.(?:com|net|org|io|tv|cc|xyz|gg|app)[-_ ]+", re.IGNORECASE
)
_EDGE_SEPARATORS = "-_. \t"
_UUID = re.compile(r"^[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}$", re.IGNORECASE)
_HEX = re.compile(r"^[0-9a-f]+$", re.IGNORECASE)
# Names cameras and apps give: IMG_1234, DSC01234, PXL_2026..., Screenshot 2026-..., photo_2026-...
# Matched against the lowercased tokens joined by single spaces.
_CAMERA = re.compile(
    r"^(?:img|dsc[nf]?|pxl|mvi|vid|mov|gopr|g[hx]|cimg|sam|wp|pano|fb img|photo|image|images|video|picture|pic"
    r"|screenshot|screen shot|screen recording|screenrecording|signal|received|snapchat"
    r"|whatsapp (?:image|video)|telegram|capture|scan|untitled|unnamed|download|file)"
    r"(?: ?(?:\d+|wa\d+|at|from|am|pm|mp|hdr|night|portrait|pano|burst|cover|edited|original))*$"
)
# these say nothing even without a number
_GENERIC_ALONE = frozenset(
    {"untitled", "unnamed", "download", "image", "images", "screenshot", "screen shot", "screen recording",
     "screenrecording"}
)
_SEGMENTS = re.compile(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+")
_TOKEN_SPLIT = re.compile(r"[\W_]+")


def _trim(stem: str) -> str:
    """The stem without download counters, a slug-id suffix, a site prefix and stray separators."""
    s = stem.strip()
    while True:
        before = s
        s = _COUNTER.sub("", s).strip(_EDGE_SEPARATORS)
        if s == before:
            break
    s = _SLUG_ID.sub("", s).strip(_EDGE_SEPARATORS)
    s = _SITE.sub("", s).strip(_EDGE_SEPARATORS)
    return s


def _tokens(s: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(s) if t]


def _segment_ok(seg: str) -> bool:
    low = seg.lower()
    if low in _SHORT_WORDS:
        return True
    if len(seg) >= 3 and any(c in _VOWELS for c in low):
        return True
    return seg.isupper() and 2 <= len(seg) <= 5  # an acronym: NYC, HD


def _wordy(token: str) -> bool:
    """Does this token read like one or more words (possibly with a number at either end)?"""
    if not token.isascii():
        return any(c.isalpha() for c in token)  # other scripts: no rules here, trust the name
    letters = token.strip("0123456789")
    if not letters or not letters.isalpha():
        return False  # no letters, or digits between letters
    if len(letters) != len(token) and len(letters) < 3:
        return False  # "x1234567", "v0"
    if not all(_segment_ok(seg) for seg in _SEGMENTS.findall(letters)):
        return False  # mixed case that is not CamelCase words: "GKtwUBnXcAAQeLp"
    if len(letters) >= MIN_MACHINE_LEN:
        low = letters.lower()
        vowels = sum(c in _VOWELS for c in low)
        if vowels * 10 <= len(low):  # at most a tenth vowels
            return False
        if re.search(r"[^aeiouy]{7,}", low):
            return False
    return True


def _machine(token: str) -> bool:
    """A long token that does not read like words."""
    return len(token) >= MIN_MACHINE_LEN and not _wordy(token)


@lru_cache(maxsize=65536)
def is_gibberish(stem: str) -> bool:
    """True for machine-made file names (ids, hashes, camera counters); False when in doubt."""
    s = _trim(stem)
    tokens = _tokens(s)
    if not tokens:
        return True  # nothing left to read
    if all(t.isdigit() for t in tokens):
        return True  # timestamps and counters
    joined = " ".join(tokens).lower()
    if _CAMERA.match(joined) and (any(c.isdigit() for c in joined) or joined in _GENERIC_ALONE):
        return True
    packed = "".join(tokens)
    if _UUID.match(s) or (len(packed) >= 16 and _HEX.match(packed)):
        return True
    if len(tokens) == 1:
        return _machine(tokens[0])
    # several tokens: an id cut up by its own "-" and "_" ("GZDMAuoWYA7R_7_"), never a name with a real word
    return any(_machine(t) for t in tokens) and not any(_wordy(t) for t in tokens)


@lru_cache(maxsize=65536)
def clean_filename(stem: str) -> str:
    """A readable name from a meaningful file name (may be empty when nothing readable is left)."""
    s = re.sub(r"[-_.]+", " ", _trim(stem))
    words = s.split()

    def bare(word: str) -> str:
        return "".join(_tokens(word))

    # a machine id at the end ("maria_Lopez_GKtwUBnXcAAQeLp"): drop it and the scraps after it
    end = len(words)
    while end > 1 and not _wordy(bare(words[end - 1])):
        end -= 1
    if any(_machine(bare(w)) for w in words[end:]):
        words = words[:end]
    s = " ".join(words)
    if len(s) > MAX_CLEAN_LEN:
        cut = s[: MAX_CLEAN_LEN + 1]
        s = (cut.rsplit(" ", 1)[0] if " " in cut else s[:MAX_CLEAN_LEN]).rstrip()
    return s[:1].upper() + s[1:]


def card_code(number: int) -> str:
    """The card's permanent number as shown: six digits after a hash ("#000123")."""
    return f"#{number:06d}"


def generated_title(number: int) -> str:
    """The name for a card whose file name says nothing: "Card" and its number."""
    return f"Card {card_code(number)}"


def display_title(stem: str, custom: str | None, number: int) -> str:
    """The card's name: the player's own title, else "Card #000123".

    v0.9.0 (decision D5): file names no longer make names. `stem` (the file name) is not used for
    the name any more; it is on the card payload as `file_name`. (`is_gibberish` and
    `clean_filename` above are kept for tools that want a readable name from a file name.)"""
    if custom and custom.strip():
        return custom.strip()
    return generated_title(number)


def normalize_title(raw: str | None) -> str | None:
    """A custom title as it is stored: trimmed, inner whitespace collapsed, 1-60 characters.

    None or an empty text means "no custom title" (returns None). ValueError for control
    characters (tabs and line breaks included) and for titles that are too long.
    """
    if raw is None:
        return None
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in raw):
        raise ValueError("A card name cannot contain control characters")
    out = " ".join(raw.split())
    if not out:
        return None
    if len(out) > MAX_TITLE_LEN:
        raise ValueError(f"A card name is at most {MAX_TITLE_LEN} characters")
    return out
