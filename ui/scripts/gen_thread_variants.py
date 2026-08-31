# -*- coding: utf-8 -*-
"""Gate 2 item: the RH/LH label is baked into the illustration in pure yellow,
the one element in the frame that ignores the theme -- and on the light theme
it is yellow on near-white.

The text separates cleanly from the artwork: the tool holder is amber
(220,191,82) and lives entirely at y <= 65, while the glyphs are pure
(255,255,0) at y >= 66. So the text can be recoloured without touching the
drawing.

Run (from ui/):

    ./.venv/bin/python scripts/gen_thread_variants.py

Writes a dark and a light variant of each hand. The UI picks between them by
BACKGROUND LUMINANCE, not by theme name, because palettes.py loads user themes
from ~/.config/reflex/themes/*.ini -- keying an asset filename on the theme's
name would 404 on any theme but the two shipped ones.

The originals stay in the repo untouched: they are the source this regenerates
from, so re-running is idempotent.
"""
import configparser
import os

from PIL import Image

# Resolved from this file's own location: scripts/ -> its parent -> reflex/.
_UI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIC = os.path.join(_UI, "reflex", "pictures") + os.sep
THEMES = os.path.join(_UI, "reflex", "themes") + os.sep

TEXT_MIN_Y = 66          # everything above this row is artwork, not the label


def theme_text_rgb(name):
    cp = configparser.ConfigParser()
    cp.read(THEMES + name + ".ini", encoding="utf-8")
    vals = [float(p) for p in cp["colors"]["text"].split(",")]
    return tuple(int(round(max(0.0, min(1.0, v)) * 255)) for v in vals[:3])


def is_label_pixel(x, y, r, g, b, a):
    """A glyph pixel of the baked-in RH/LH text.

    Position AND colour, both required. Position alone would take the black
    tool body that shares those rows; colour alone would take the amber tool
    holder at y 43..65.
    """
    if y < TEXT_MIN_Y or a < 8:
        return False
    return r > 60 and g > 60 and abs(r - g) < 40 and b < r * 0.6


def convert(src, dst, rgb):
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    px = im.load()
    hit = 0
    for y in range(TEXT_MIN_Y, h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not is_label_pixel(x, y, r, g, b, a):
                continue
            # Preserve the glyph's own anti-aliasing: the pixel's brightness
            # relative to the pure colour becomes the blend toward the page,
            # applied to alpha rather than to RGB, so a half-lit edge pixel
            # stays a half-lit edge pixel instead of turning into a mid-tone
            # that reads as a different colour.
            lit = max(r, g) / 255.0
            px[x, y] = (rgb[0], rgb[1], rgb[2], int(round(a * lit)))
            hit += 1
    im.save(dst)
    return hit


DARK = theme_text_rgb("dark")
LIGHT = theme_text_rgb("light")
print(f"dark text {DARK}   light text {LIGHT}")

total = 0
for hand in ("thread_rh", "thread_lh"):
    src = PIC + hand + ".png"
    assert os.path.exists(src), "missing source %s" % src
    for suffix, rgb in (("_dark", DARK), ("_light", LIGHT)):
        n = convert(src, PIC + hand + suffix + ".png", rgb)
        print(f"  {hand}{suffix}.png  {n} label pixels recoloured")
        assert n > 50, "%s%s: only %d label pixels -- the glyphs were not found" % (
            hand, suffix, n)
        total += n

# ── gate: the artwork must be untouched, and the label must have changed ──
for hand in ("thread_rh", "thread_lh"):
    a = Image.open(PIC + hand + ".png").convert("RGBA")
    for suffix in ("_dark", "_light"):
        b = Image.open(PIC + hand + suffix + ".png").convert("RGBA")
        assert a.size == b.size, "%s%s changed size" % (hand, suffix)
        pa, pb = a.load(), b.load()
        above = sum(1 for y in range(TEXT_MIN_Y) for x in range(a.size[0])
                    if pa[x, y] != pb[x, y])
        below = sum(1 for y in range(TEXT_MIN_Y, a.size[1]) for x in range(a.size[0])
                    if pa[x, y] != pb[x, y])
        assert above == 0, ("%s%s: %d ARTWORK pixels changed above y=%d"
                            % (hand, suffix, above, TEXT_MIN_Y))
        assert below > 50, ("%s%s: only %d pixels changed in the label rows"
                            % (hand, suffix, below))
        print(f"  {hand}{suffix}: artwork identical, {below} label px changed")

print(f"\n{total} label pixels recoloured across 4 variants")
