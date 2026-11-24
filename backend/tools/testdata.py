"""Seed a disposable profile (own DB + media folder) for manual and browser testing.

    cd backend
    .venv\Scripts\python -m tools.testdata --profile test --reset --images 12 --credit 1000

Then run `start-test.bat` (ports 8100 / 5273). Never touches the `main` (player) profile:
everything is written under `profiles/<name>/` only.
"""

import argparse
import colorsys
import os
import random
import re
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SAMPLES = Path(__file__).resolve().parent / "sample_media"
_SIZE = (900, 1200)
_MARKER = ".mco-profile"


def _make_png(path: Path, index: int, total: int) -> None:
    from PIL import Image, ImageDraw

    rng = random.Random(index)
    hue = (index / max(total, 1) + rng.random() * 0.05) % 1.0
    w, h = _SIZE
    img = Image.new("RGB", _SIZE)
    draw = ImageDraw.Draw(img)
    top = colorsys.hsv_to_rgb(hue, 0.75, 0.95)
    bottom = colorsys.hsv_to_rgb((hue + 0.15) % 1.0, 0.85, 0.35)
    for y in range(h):
        t = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(255 * (a + (b - a) * t)) for a, b in zip(top, bottom)))
    for _ in range(6):  # a few translucent-looking shapes so every image is distinct
        c = colorsys.hsv_to_rgb((hue + rng.random() * 0.4) % 1.0, 0.6, 1.0)
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(80, 260)
        draw.ellipse([x - r, y - r, x + r, y + r], outline=tuple(int(255 * v) for v in c), width=10)
    draw.text((40, 40), f"#{index:03d}", fill=(255, 255, 255))
    img.save(path)


_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def check_base(base: Path, root: Path) -> str | None:
    """Return a refusal reason if `base` could touch the player's data or the repo itself."""
    base, root = base.resolve(), root.resolve()
    if base == root or base in root.parents:
        return f"{base} is the repo folder (or contains it)"
    for protected in (root / "data", root / "media"):
        if base == protected or protected in base.parents or base in protected.parents:
            return f"{base} overlaps the player's {protected.name} folder"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed a disposable test profile")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--reset", action="store_true", help="delete profiles/NAME/ first")
    ap.add_argument("--images", type=int, default=24)
    ap.add_argument("--videos", type=int, default=0)
    ap.add_argument("--credit", type=int, default=0, help="extra coins to add")
    args = ap.parse_args(argv)

    if args.profile == "main":
        print("Refusing to touch the main (player) profile.", file=sys.stderr)
        return 2
    if not _NAME_RE.match(args.profile) or args.profile in ("data", "media"):
        print(f"Refusing: invalid profile name {args.profile!r} (use a-z, 0-9, '-'; not data/media).",
              file=sys.stderr)
        return 2

    # Must be set before any app.* import so settings point at the profile.
    os.environ["MCO_PROFILE"] = args.profile
    for var in ("MCO_DATA_DIR", "MCO_MEDIA_DIR", "MCO_DATABASE_URL"):
        os.environ.pop(var, None)  # explicit overrides could point outside the profile

    profiles_dir = Path(os.environ.get("MCO_PROFILES_DIR") or _ROOT / "profiles").resolve()
    base = profiles_dir / args.profile
    reason = check_base(base, _ROOT)
    if reason:
        print(f"Refusing: {reason}.", file=sys.stderr)
        return 2

    from app.config import settings  # noqa: E402

    # Never trust env/.env values for locations: force everything into the profile folder
    # before app.db (which builds the engine from settings) is imported.
    settings.media_dir = base / "media"
    settings.data_dir = base / "data"
    db_file = base / "data" / "app.db"
    settings.database_url = f"sqlite:///{db_file.as_posix()}"
    if base.resolve() not in Path(settings.database_url.removeprefix("sqlite:///")).resolve().parents:
        print("Refusing: database path is outside the profile folder.", file=sys.stderr)
        return 2

    root_profiles = (_ROOT / "profiles").resolve()
    if args.reset and base.exists():
        under_profiles = profiles_dir in base.parents
        if not under_profiles or not (root_profiles in base.parents or (base / _MARKER).is_file()):
            print(f"Refusing --reset: {base} is not a profile folder made by this tool "
                  f"(no {_MARKER} marker and not under {root_profiles}).", file=sys.stderr)
            return 2
        shutil.rmtree(base)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (base / _MARKER).write_text("Disposable profile created by tools.testdata.\n", encoding="utf-8")

    for i in range(1, args.images + 1):
        _make_png(settings.media_dir / f"img_{i:03d}.png", i, args.images)

    videos = 0
    if args.videos:
        samples = sorted(_SAMPLES.glob("*.mp4")) if _SAMPLES.is_dir() else []
        if len(samples) < args.videos:  # never seed fewer videos than asked without saying so
            print(f"--videos {args.videos}: only {len(samples)} sample video(s) in {_SAMPLES}. "
                  "Put .mp4 files there first.", file=sys.stderr)
            return 2
        for src in samples[: args.videos]:
            shutil.copy2(src, settings.media_dir / src.name)
            videos += 1

    if args.credit:
        from app.db import SessionLocal, engine, migrate
        from app.services import wallet

        migrate(engine)
        with SessionLocal() as db:
            wallet.credit(db, wallet.local_user(db), args.credit, "dev_test")
            db.commit()

    print(f"Profile '{args.profile}': {args.images} images, {videos} videos, credit {args.credit} -> {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
