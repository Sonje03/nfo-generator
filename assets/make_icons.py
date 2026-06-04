"""
Generate the NFO Generator app icons (.png, .icns, .ico) from a single
parametric design. Run once after editing any of the visual parameters.

Output: ``assets/icon.png``, ``assets/icon.icns``, ``assets/icon.ico``.
PyInstaller picks these up via ``--icon=...`` (see the GitHub Actions
workflow at ``.github/workflows/release.yml``).

Design:

- 1024 x 1024 base raster, rounded-square mask (18% corner radius).
- Dark backdrop with a subtle vertical gradient.
- Three centered block-style letters "NFO" rendered from block characters
  on a 5 × 7 pixel grid (same look as the project's `pyfiglet "block"`
  banners). Letters are filled with a teal accent.
- A pair of bracket-quote glyphs (`{ }`) below the letters to evoke the
  metadata-stream nature of an .nfo file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError as exc:  # pragma: no cover
    print("Pillow is required: pip install pillow", file=sys.stderr)
    raise SystemExit(1) from exc


# --- Visual parameters --------------------------------------------------------

SIZE = 1024                       # master raster size in pixels
CORNER_RADIUS = int(SIZE * 0.18)  # rounded-square corner radius

BG_TOP    = (24, 28, 40)          # near-black, slightly cool
BG_BOTTOM = (12, 14, 22)          # darker bottom for the gradient
ACCENT    = (64, 204, 196)        # teal — the "block letter" fill
SUBTLE    = (40, 80, 100)         # faded teal for the sub-decoration


# --- 5 × 7 pixel font for the letters N, F, O --------------------------------
# Each glyph is a list of 7 strings, each 5 chars long; '█' = filled pixel.
GLYPHS = {
    "N": [
        "█   █",
        "██  █",
        "█ █ █",
        "█  ██",
        "█   █",
        "█   █",
        "█   █",
    ],
    "F": [
        "█████",
        "█    ",
        "█    ",
        "████ ",
        "█    ",
        "█    ",
        "█    ",
    ],
    "O": [
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ],
}


def _gradient_background(size: int) -> Image.Image:
    """Vertical gradient from BG_TOP to BG_BOTTOM."""
    image = Image.new("RGB", (size, size), BG_TOP)
    pixels = image.load()
    for y in range(size):
        t = y / (size - 1)
        r = int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
        for x in range(size):
            pixels[x, y] = (r, g, b)
    return image


def _rounded_mask(size: int, radius: int) -> Image.Image:
    """White rounded-square on black, used as an alpha mask."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255,
    )
    return mask


def _draw_letters(image: Image.Image) -> None:
    """Rasterise N F O centered horizontally within the canvas."""
    draw = ImageDraw.Draw(image)
    letters = ["N", "F", "O"]
    glyph_cols = 5
    glyph_rows = 7

    # Derive the cell size from the desired total width rather than from a
    # fixed % of SIZE — that way the row of letters fits properly inside the
    # canvas no matter how many letters / gaps we use.
    target_total_width = int(SIZE * 0.78)        # ≈ 22 % side margins
    gap_ratio = 1.2                              # gap = cell × gap_ratio
    # total = N letters × (5 cells) + (N − 1) gaps × (gap_ratio cells)
    total_cells = len(letters) * glyph_cols + (len(letters) - 1) * gap_ratio
    cell = target_total_width / total_cells
    cell_int = max(1, int(cell))                 # integer cell size for the rects
    gap_px = int(cell * gap_ratio)
    letter_w = glyph_cols * cell_int

    total_w = len(letters) * letter_w + (len(letters) - 1) * gap_px
    x0 = (SIZE - total_w) // 2
    # Vertically centered upper-band: aim for ~ rows 0.20 → 0.20 + 7×cell/SIZE.
    glyph_h = glyph_rows * cell_int
    y0 = int((SIZE - glyph_h) * 0.42)            # slight upward bias so brackets fit

    for li, letter in enumerate(letters):
        rows = GLYPHS[letter]
        lx = x0 + li * (letter_w + gap_px)
        for ry, row in enumerate(rows):
            for cx, ch in enumerate(row):
                if ch == "█":
                    px = lx + cx * cell_int
                    py = y0 + ry * cell_int
                    # 1 px gutter between pixels for a "blocky LED" look
                    draw.rectangle(
                        (px, py, px + cell_int - 2, py + cell_int - 2),
                        fill=ACCENT,
                    )


def _draw_brackets(image: Image.Image) -> None:
    """Two `{ }` markers below the letters — metadata-stream hint."""
    draw = ImageDraw.Draw(image)
    y = int(SIZE * 0.78)
    h = int(SIZE * 0.10)
    bar = max(6, int(SIZE * 0.012))
    span = int(SIZE * 0.32)
    cx = SIZE // 2

    def _bracket(left_x: int, right_x: int, opening: bool) -> None:
        # vertical bar
        vx = right_x if opening else left_x
        draw.rectangle(
            (vx - bar // 2, y, vx + bar // 2, y + h),
            fill=SUBTLE,
        )
        # two short horizontal "wings"
        wing = int(span * 0.20)
        draw.rectangle(
            (vx - (wing if opening else 0),
             y - bar // 2,
             vx + (0 if opening else wing),
             y + bar // 2),
            fill=SUBTLE,
        )
        draw.rectangle(
            (vx - (wing if opening else 0),
             y + h - bar // 2,
             vx + (0 if opening else wing),
             y + h + bar // 2),
            fill=SUBTLE,
        )

    _bracket(cx - span,           cx - int(span * 0.55), opening=True)
    _bracket(cx + int(span * 0.55), cx + span,           opening=False)


def build_master(size: int = SIZE) -> Image.Image:
    """Return the 1024×1024 RGBA master icon."""
    bg = _gradient_background(size)
    _draw_letters(bg)
    _draw_brackets(bg)

    # Apply the rounded-square alpha mask.
    rgba = bg.convert("RGBA")
    mask = _rounded_mask(size, CORNER_RADIUS)
    rgba.putalpha(mask)

    # Optional gentle inner glow on the letters.
    rgba = rgba.filter(ImageFilter.SMOOTH)
    return rgba


def write_outputs(master: Image.Image, out_dir: Path) -> None:
    """Write icon.png, icon.icns, icon.ico into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)

    png_path = out_dir / "icon.png"
    master.save(png_path, format="PNG")
    print(f"  wrote {png_path}  ({png_path.stat().st_size} bytes)")

    # .icns — Pillow handles multi-size internally.
    icns_path = out_dir / "icon.icns"
    master.save(icns_path, format="ICNS")
    print(f"  wrote {icns_path}  ({icns_path.stat().st_size} bytes)")

    # .ico — explicit multi-size list for Windows.
    ico_path = out_dir / "icon.ico"
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ico_path, format="ICO", sizes=sizes)
    print(f"  wrote {ico_path}  ({ico_path.stat().st_size} bytes)")


def main() -> int:
    out_dir = Path(__file__).resolve().parent
    print("Generating icons in", out_dir)
    master = build_master(SIZE)
    write_outputs(master, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
