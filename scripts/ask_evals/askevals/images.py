"""Images the harness draws for itself (spec §5.3): never photographed, so each check knows what
is in its image. Pillow is the only dependency, and the font is DejaVu Sans when the system has it.

Kinds:
- `blank`: one flat colour, nothing to read.
- `noise`: random grey pixels with no text (seeded, so the same scenario draws the same image).
- `page`: a product page with a known name and price, drawn in dark text on white.
- `faint`: the same page at very low contrast, which an OCR reader returns nothing from.
- `chart`: a bar chart with labelled bars and a title.
- `tiny`: a 1x1 image.
- `truncated`: a PNG cut off after its first bytes, which no reader can open.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont

FONT_PATHS: Final[tuple[str, ...]] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)
SIZE: Final[tuple[int, int]] = (900, 600)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _page(spec: dict, ink: tuple[int, int, int], paper: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", SIZE, paper)
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), spec.get("shop", "Example Shop"), fill=ink, font=_font(28))
    draw.text((40, 110), spec["name"], fill=ink, font=_font(40))
    draw.text((40, 190), spec["price"], fill=ink, font=_font(48))
    y = 280
    for line in spec.get("lines", []):
        draw.text((40, y), line, fill=ink, font=_font(26))
        y += 44
    return image


def draw(kind: str, path: Path, spec: dict | None = None, seed: int = 0) -> Path:
    """Draws one image of `kind` to `path` (PNG) and returns the path."""
    spec = spec or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "blank":
        Image.new("RGB", SIZE, tuple(spec.get("colour", (245, 245, 245)))).save(path)
    elif kind == "noise":
        rng = random.Random(seed)
        image = Image.new("L", SIZE)
        image.putdata([rng.randrange(256) for _ in range(SIZE[0] * SIZE[1])])
        image.save(path)
    elif kind == "page":
        _page(spec, (20, 20, 20), (255, 255, 255)).save(path)
    elif kind == "faint":
        _page(spec, (250, 250, 250), (255, 255, 255)).save(path)
    elif kind == "chart":
        image = Image.new("RGB", SIZE, (255, 255, 255))
        d = ImageDraw.Draw(image)
        d.text((40, 20), spec.get("title", "Chart"), fill=(0, 0, 0), font=_font(30))
        bars = spec.get("bars", {"A": 3, "B": 5})
        top = max(bars.values()) or 1
        width = (SIZE[0] - 120) // max(1, len(bars))
        for i, (label, value) in enumerate(bars.items()):
            x = 60 + i * width
            height = int(400 * value / top)
            d.rectangle((x, 520 - height, x + width - 30, 520), fill=(60, 110, 180))
            d.text((x, 530), f"{label}: {value}", fill=(0, 0, 0), font=_font(22))
        image.save(path)
    elif kind == "tiny":
        Image.new("RGB", (1, 1), (255, 255, 255)).save(path)
    elif kind == "truncated":
        full = path.with_suffix(".full.png")
        _page({"name": "x", "price": "y"}, (0, 0, 0), (255, 255, 255)).save(full)
        path.write_bytes(full.read_bytes()[:64])
        full.unlink()
    else:
        raise ValueError(f"unknown image kind {kind!r}")
    return path
