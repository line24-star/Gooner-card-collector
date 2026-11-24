"""Which files count as media."""

from pathlib import Path
from typing import Literal

IMAGE_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi"})


MAX_TAG_LEN = 40


def normalize_tag(name: str) -> str:
    """Trim, collapse inner whitespace, lowercase; 1-40 characters or ValueError."""
    out = " ".join(name.split()).lower()
    if not out:
        raise ValueError("Tag name is empty")
    if len(out) > MAX_TAG_LEN:
        raise ValueError(f"Tag name is longer than {MAX_TAG_LEN} characters")
    return out


def classify(path: Path) -> Literal["image", "video"] | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None
