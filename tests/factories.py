"""Generated test media.

Every fixture here is produced at test time, never committed. That is partly
policy — `.gitignore` excludes every image and video extension, so binary
fixtures literally cannot be checked in — and partly correctness: the pipeline
computes perceptual hashes and reads EXIF, and mocking either would test the
mocks rather than the behaviour.

Images are real JPEGs. Dates are written with real exiftool. What the tests
exercise is what production does.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw


def make_image(
    path: Path,
    size: tuple[int, int] = (640, 480),
    seed: int = 0,
    quality: int = 90,
) -> Path:
    """Write a JPEG with deterministic, visually distinctive content.

    Content varies with ``seed`` so different seeds produce genuinely different
    perceptual hashes — a flat colour field would hash alike regardless, which
    would make the dedupe tests meaningless.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    image = Image.new("RGB", size, (16 + (seed * 37) % 200, 32, 64))
    draw = ImageDraw.Draw(image)

    # A handful of shapes whose positions depend on the seed. Enough structure
    # for a perceptual hash to latch onto.
    for i in range(6):
        offset = (seed * 53 + i * 71) % max(width // 2, 1)
        box = (
            offset,
            (seed * 29 + i * 43) % max(height // 2, 1),
            offset + width // 3,
            height // 2 + (i * 17) % max(height // 3, 1),
        )
        draw.rectangle(box, fill=((seed * 91 + i * 40) % 256, (i * 60) % 256, (seed * 17) % 256))
    draw.ellipse(
        (width // 4, height // 4, width // 2, height // 2),
        fill=(240, 200 - seed % 100, 30),
    )

    image.save(path, "JPEG", quality=quality)
    return path


def downscale(source: Path, destination: Path, factor: int = 4, quality: int = 70) -> Path:
    """Write a smaller, more compressed copy — a low-resolution twin.

    This is the one near-duplicate case the pipeline resolves automatically, so
    it needs a fixture that is genuinely the same picture at genuinely smaller
    dimensions.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        smaller = image.resize((image.width // factor, image.height // factor))
        smaller.save(destination, "JPEG", quality=quality)
    return destination


def copy_file(source: Path, destination: Path) -> Path:
    """A byte-identical duplicate."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def set_exif_date(path: Path, taken_at: datetime) -> None:
    """Stamp a real EXIF DateTimeOriginal using exiftool."""
    stamp = taken_at.strftime("%Y:%m:%d %H:%M:%S")
    subprocess.run(
        [
            "exiftool",
            "-overwrite_original",
            f"-DateTimeOriginal={stamp}",
            f"-CreateDate={stamp}",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def make_dated_image(
    path: Path, taken_at: datetime, size: tuple[int, int] = (640, 480), seed: int = 0
) -> Path:
    make_image(path, size=size, seed=seed)
    set_exif_date(path, taken_at)
    return path


def make_sidecar(media: Path, taken_at: datetime, persons: list[str] | None = None) -> Path:
    """Write an osxphotos-style JSON sidecar next to a media file."""
    import json

    sidecar = media.with_suffix(media.suffix + ".json")
    sidecar.write_text(
        json.dumps(
            {
                "date": taken_at.isoformat(),
                "albums": [],
                "persons": persons or [],
            }
        ),
        encoding="utf-8",
    )
    return sidecar


def make_fake_video(path: Path, payload: bytes = b"not-really-a-video") -> Path:
    """A file with a video extension and no readable metadata.

    Stands in for the undated-video case: the pipeline must treat it as undated
    and queue it, not invent a date from the filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path
