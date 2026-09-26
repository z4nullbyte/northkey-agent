#!/usr/bin/env python3
"""Generate the Northkey terminal marks and audit the palette.

    python northkey/design/marks.py            # print banner_logo + banner_hero (Rich markup)
    python northkey/design/marks.py --audit    # WCAG audit of northkey/skins/*.yaml

Everything is drawn in braille dots (U+2800..U+28FF) plus the ✦ star: glyphs
whose East Asian Width is Neutral, so they stay one cell wide in every locale.
The output is pasted into northkey/skins/northkey.yaml (banner_logo/banner_hero).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

BLANK = "⠀"
STAR = "✦"

# 5x8 dot glyphs: thin geometric capitals, one-dot strokes.
GLYPHS = {
    "N": ["#...#", "##..#", "##..#", "#.#.#", "#.#.#", "#..##", "#..##", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#", "#...#"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#", "#...#"],
    "K": ["#...#", "#..#.", "#.#..", "##...", "##...", "#.#..", "#..#.", "#...#"],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#....", "#####"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#..", "..#.."],
}
# Braille dot bit for (x, y) inside a 2x4 cell.
_BITS = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
         (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80}

# Mid-luminance art colors: bright on dark terminals, still visible on light ones.
HERO_COLORS = ["#D9C29C", "#D9C29C", "#CDBB9A", "#BFB6A6", "#AEB1B6", "#8CCFE0",
               "#8CCFE0", "#9AA3AF", "#8A919C", "#7A828E", "#6B7380"]
LOGO_COLORS = ["#E6E8EB", "#C9CED6"]


def to_braille(pixels: list[list[bool]]) -> list[str]:
    height, width = len(pixels), len(pixels[0])
    rows = []
    for top in range(0, height, 4):
        line = []
        for left in range(0, width, 2):
            code = 0x2800
            for (dx, dy), bit in _BITS.items():
                y, x = top + dy, left + dx
                if y < height and x < width and pixels[y][x]:
                    code |= bit
            line.append(chr(code))
        rows.append("".join(line))
    return rows


def wordmark(text: str = "NORTHKEY", tracking: int = 3) -> list[str]:
    """Letter-spaced dot-matrix capitals, two braille rows tall."""
    width = len(text) * (5 + tracking) - tracking
    width += width % 2
    pixels = [[False] * width for _ in range(8)]
    for i, ch in enumerate(text):
        for y, row in enumerate(GLYPHS[ch]):
            for x, dot in enumerate(row):
                if dot == "#":
                    pixels[y][i * (5 + tracking) + x] = True
    return to_braille(pixels)


def compass(size: int = 49) -> list[str]:
    """Compass rose: hairline needle (north longest), outline star, gapped bezel."""
    north, south, side, p, scale = 1.0, 0.72, 0.78, 0.45, 0.7
    ring_r, ring_w, gap = 0.6, 0.028, 0.12
    width = size + (size % 2)
    pixels = [[False] * width for _ in range(size)]
    c = (size - 1) / 2
    for y in range(size):
        for x in range(size):
            dx, dy = (x - c) / c, (y - c) / c
            reach = (north if dy < 0 else south) * scale
            v = (abs(dx) / (side * scale)) ** p + (abs(dy) / reach) ** p
            outline = 0.8 <= v <= 1.0
            bezel = (abs(math.hypot(dx, dy) - ring_r) < ring_w and v > 1.0
                     and min(abs(dx), abs(dy)) >= gap)
            needle = (x == round(c) and -north <= dy <= south) or (y == round(c) and abs(dx) <= side)
            pixels[y][x] = outline or bezel or needle
    rows = to_braille(pixels)
    while rows and set(rows[0]) <= {BLANK}:
        rows.pop(0)
    while rows and set(rows[-1]) <= {BLANK}:
        rows.pop()
    return rows


def rich_hero() -> str:
    rows = compass()
    lines = [f"[{HERO_COLORS[min(i, len(HERO_COLORS) - 1)]}]{row}[/]" for i, row in enumerate(rows)]
    caption = "pointed north"
    pad = max(0, len(rows[0]) - len(caption))
    lines.append(f"[dim #8A919C]{BLANK * (pad // 2)}{caption}{BLANK * (pad - pad // 2)}[/]")
    return "\n".join(lines)


def rich_logo() -> str:
    rows = wordmark()
    lead = [f"[bold #D9C29C]{STAR}[/]", " "]
    return "\n".join(f"{lead[i]}  [{LOGO_COLORS[i]}]{row}[/]" for i, row in enumerate(rows))


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")

    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def audit() -> int:
    """Print each skin color's contrast vs the terminal poles upstream's tests assume."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import hermes_yaml as yaml

    for path in sorted((Path(__file__).resolve().parents[1] / "skins").glob("*.yaml")):
        skin = yaml.safe_load(path.read_text(encoding="utf-8"))
        for block, pole in (("colors", "#101014"), ("light_colors", "#ffffff")):
            print(f"{skin['name']}.{block} vs {pole}")
            for key, value in (skin.get(block) or {}).items():
                print(f"  {key:28} {value}  {contrast(value, pole):5.2f}:1")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--audit" in sys.argv:
        return audit()
    print("banner_logo: |-")
    for line in rich_logo().splitlines():
        print("  " + line)
    print("banner_hero: |-")
    for line in rich_hero().splitlines():
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
