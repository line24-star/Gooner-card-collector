"""Cached WebP thumbnails. Written only under data_dir, never inside the media folder."""

import logging
import os
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from ..config import settings

log = logging.getLogger("mco.thumbs")

THUMB_MAX = 720  # full-art cards fill the whole face; ~720 px keeps md/lg sharp at 2x DPR
THUMB_QUALITY = 80


def thumb_path(sha256: str) -> Path:
    # The size is in the name, so changing THUMB_MAX regenerates thumbnails lazily on next request.
    return settings.data_dir / "thumbs" / sha256[:2] / f"{sha256}-{THUMB_MAX}.webp"


def ensure_thumb(src: Path, sha256: str) -> Path | None:
    dest = thumb_path(sha256)
    if dest.exists():
        return dest
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(src) as im:
            im.seek(0)  # first frame for animated formats
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(tmp, "WEBP", quality=THUMB_QUALITY)
        os.replace(tmp, dest)
    except Exception:  # contract: never raise for a bad source; Pillow can raise many types
        log.warning("thumbnail failed for %s", src, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return dest
