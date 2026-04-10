"""Generate the TangoSuggest vinyl-record icon and produce a .icns file.

Run from the repo root:
    uv run python scripts/make_icon.py

Requires: Pillow  (in the dev dependency group)
macOS only: uses iconutil to produce the final .icns
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).parent.parent
RESOURCES_DIR = REPO_ROOT / "src" / "tanda_suggester" / "gui" / "resources"
ICONSET_DIR = REPO_ROOT / "TangoSuggest.iconset"
ICNS_OUTPUT = RESOURCES_DIR / "TangoSuggest.icns"

# Icon sizes required by macOS iconsets
SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw_vinyl(size: int) -> Image.Image:
    """Draw a vinyl record icon at the given pixel size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = size / 2
    r = size / 2

    # Outer vinyl disc — near-black with a very slight warm tint
    draw.ellipse([0, 0, size - 1, size - 1], fill=(18, 14, 12, 255))

    # Groove rings — alternate between slightly lighter and darker bands
    # Spread from ~55% radius down to the label edge (~28% radius)
    groove_outer = r * 0.97
    label_r = r * 0.28
    groove_inner = r * 0.30
    num_grooves = max(2, int(size / 24))
    step = (groove_outer - groove_inner) / num_grooves

    for i in range(num_grooves):
        ring_outer = groove_outer - i * step
        ring_inner = ring_outer - step * 0.45
        shade = 38 + (i % 2) * 10  # slight alternating shimmer
        draw.ellipse(
            [cx - ring_outer, cy - ring_outer, cx + ring_outer, cy + ring_outer],
            fill=(shade, shade - 2, shade - 4, 255),
        )
        draw.ellipse(
            [cx - ring_inner, cy - ring_inner, cx + ring_inner, cy + ring_inner],
            fill=(18, 14, 12, 255),
        )

    # Centre label — warm gold
    label_color = (212, 168, 83, 255)   # #D4A853
    draw.ellipse(
        [cx - label_r, cy - label_r, cx + label_r, cy + label_r],
        fill=label_color,
    )

    # Spindle hole
    spindle_r = max(1, r * 0.028)
    draw.ellipse(
        [cx - spindle_r, cy - spindle_r, cx + spindle_r, cy + spindle_r],
        fill=(18, 14, 12, 255),
    )

    # "TS" text on label — only render if large enough to be legible
    if size >= 64:
        font_size = int(label_r * 0.75)
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont
        try:
            # Try a bold system font
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", font_size)
        except (OSError, AttributeError):
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
            except (OSError, AttributeError):
                font = ImageFont.load_default()

        text = "TS"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = cx - tw / 2 - bbox[0]
        ty = cy - th / 2 - bbox[1]
        draw.text((tx, ty), text, font=font, fill=(80, 50, 10, 255))

    return img


def make_iconset() -> None:
    """Produce all required PNG sizes in the .iconset directory."""
    ICONSET_DIR.mkdir(exist_ok=True)
    for size in SIZES:
        img = draw_vinyl(size)
        img.save(ICONSET_DIR / f"icon_{size}x{size}.png")
        if size <= 512:
            # @2x retina variant
            img2x = draw_vinyl(size * 2)
            img2x.save(ICONSET_DIR / f"icon_{size}x{size}@2x.png")
    print(f"Iconset written to {ICONSET_DIR}")


def make_icns() -> None:
    """Convert the iconset to .icns using macOS iconutil."""
    if sys.platform != "darwin":
        print("Warning: iconutil is macOS-only; skipping .icns generation.")
        print(f"PNGs are in {ICONSET_DIR} — convert manually if needed.")
        return

    if not shutil.which("iconutil"):
        print("iconutil not found — skipping .icns generation.")
        return

    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["iconutil", "--convert", "icns", str(ICONSET_DIR), "--output", str(ICNS_OUTPUT)],
        check=True,
    )
    print(f"Icon written to {ICNS_OUTPUT}")

    # Clean up the temporary iconset directory
    shutil.rmtree(ICONSET_DIR)
    print("Cleaned up temporary iconset directory.")


if __name__ == "__main__":
    make_iconset()
    make_icns()
