"""Theme palettes — discovered from per-theme ``.ini`` files (one theme per file)
so users can tweak colors in a text editor and exchange themes as single files.

Themes are loaded from two directories:

- **built-in**: ``reflex/themes/*.ini`` (shipped: dark, light)
- **user**:     ``~/.config/reflex/themes/*.ini`` (drop a file here to add or, by
                reusing a built-in name, override a theme)

Each file is a small INI (parsed with the stdlib ``configparser`` — the same
INI family already used for ``config.ini``; no new dependency, comment-friendly,
and forgiving for hand-editing color tuples). Sections:

    [meta]    name, label
    [colors]  every color token = r, g, b, a   (0..1)
    [paths]   logo + font_* asset paths
    [seeds]   operator color seeds applied on theme switch (display_color,
              color_on, color_off)

This module preserves its old public API — ``PALETTES`` (name -> palette dict of
all tokens), ``FORMATS_RECOMMENDED`` (name -> seed colors), ``DEFAULT`` — so the
``ThemeProvider`` and app wiring are unchanged. A theme missing a token inherits
it from the default theme (logged), so a partial user file still loads.
"""

import os
import configparser

from kivy.logger import Logger

import reflex

log = Logger.getChild(__name__)

# Canonical token sets (kept in lockstep with ThemeProvider's properties).
COLOR_TOKENS = [
    "background", "surface", "surface_sheen", "recess",
    "accent", "accent_text", "accent_bg", "glow",
    "border", "border_dim", "edge_dark", "edge_light",
    "text", "text_dim", "text_disabled",
    "success", "success_text", "success_bg",
    "danger", "danger_text", "danger_bg", "danger_glow",
    "warning", "led_off",
    "plot_bg", "plot_grid", "plot_tool",
]
PATH_TOKENS = ["logo", "font_bold", "font_mono", "font_seg", "font_icon"]
SEED_TOKENS = ["display_color", "color_on", "color_off"]

DEFAULT = "dark"

BUILTIN_DIR = os.path.join(os.path.dirname(reflex.__file__), "themes")
USER_DIR = os.path.expanduser("~/.config/reflex/themes")


def _parse_color(text: str) -> list[float]:
    parts = [p for p in text.replace(";", ",").split(",") if p.strip() != ""]
    vals = [float(p) for p in parts]
    if len(vals) == 3:
        vals.append(1.0)
    if len(vals) != 4:
        raise ValueError(f"expected 3 or 4 numbers, got {text!r}")
    return vals


def _load_file(path: str, name: str):
    """Parse one theme file -> (palette, seeds, label). Raises on bad files.

    The theme's identity is the file name; ``[meta] label`` is only its display
    name."""
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    label = cp.get("meta", "label", fallback=name.title())

    palette = {}
    if cp.has_section("colors"):
        for k, v in cp.items("colors"):
            palette[k] = _parse_color(v)
    if cp.has_section("paths"):
        for k, v in cp.items("paths"):
            palette[k] = v.strip()

    seeds = {}
    if cp.has_section("seeds"):
        for k, v in cp.items("seeds"):
            seeds[k] = _parse_color(v)
    return palette, seeds, label


def _discover() -> dict[str, str]:
    """Map theme name -> file path. User files override built-ins by name."""
    found = {}
    for d in (BUILTIN_DIR, USER_DIR):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".ini"):
                found[os.path.splitext(fn)[0]] = os.path.join(d, fn)
    return found


def _load_all():
    palettes, recommended, labels = {}, {}, {}
    for name, path in _discover().items():
        try:
            palette, seeds, label = _load_file(path, name)
        except Exception as e:
            log.warning(f"Skipping theme file {path}: {e}")
            continue
        palettes[name] = palette
        recommended[name] = seeds
        labels[name] = label

    if not palettes:
        raise RuntimeError(f"No theme files found in {BUILTIN_DIR}")

    # Pick a default reference to backfill any tokens a theme omits.
    ref_name = DEFAULT if DEFAULT in palettes else next(iter(palettes))
    ref = palettes[ref_name]
    missing_ref = [t for t in COLOR_TOKENS + PATH_TOKENS if t not in ref]
    if missing_ref:
        raise RuntimeError(
            f"Built-in default theme '{ref_name}' is missing tokens: {missing_ref}"
        )
    for name, palette in palettes.items():
        for token in COLOR_TOKENS + PATH_TOKENS:
            if token not in palette:
                log.warning(f"Theme '{name}' missing '{token}'; using '{ref_name}' value")
                palette[token] = ref[token]
        for token in SEED_TOKENS:
            recommended[name].setdefault(token, recommended[ref_name].get(token))

    return palettes, recommended, labels, ref_name


PALETTES, FORMATS_RECOMMENDED, LABELS, DEFAULT = _load_all()
