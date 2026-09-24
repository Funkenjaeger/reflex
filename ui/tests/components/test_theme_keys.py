"""Every theme defines every key, and every key the kv tree uses exists.

Two ways a contrast fix made in one theme silently fails to reach the screen:

  * a built-in theme file omits a token. palettes._load_all() then BACKFILLS
    it from the default (dark) theme with only a log warning -- so a light
    theme missing `text_disabled` would draw dark's pale disabled grey on the
    light page, and every contrast test that reads PALETTES would be checking
    the backfilled value, not anything the light theme chose;
  * a kv rule names a key ThemeProvider does not have (a typo, or a key
    added to one theme file but never to the provider). That is only an
    AttributeError when the screen is first built -- on the machine.

Added 2026-09-19 with the Setup-screen contrast work (the fixes lean on
text / text_disabled / success_text / danger_text being real, per-theme
values in both themes).
"""
import configparser
import os
import re

import pytest

import reflex
from reflex.components.widgets import palettes
from reflex.components.widgets.theme_provider import ThemeProvider

UI = os.path.dirname(reflex.__file__)
REQUIRED = {"colors": palettes.COLOR_TOKENS, "paths": palettes.PATH_TOKENS}
THEME_REF = re.compile(r"\bapp\.theme\.([A-Za-z_]\w*)")


def missing_tokens(ini_text):
    cp = configparser.ConfigParser()
    cp.read_string(ini_text)
    out = []
    for section, tokens in REQUIRED.items():
        have = set(cp.options(section)) if cp.has_section(section) else set()
        out += [f"[{section}] {t}" for t in tokens if t not in have]
    return out


def unknown_theme_refs(kv_text):
    known = set(dir(ThemeProvider))
    return sorted({k for k in THEME_REF.findall(kv_text) if k not in known})


def _builtin_inis():
    d = palettes.BUILTIN_DIR
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".ini"))


def _kv_files():
    for root, _dirs, files in os.walk(UI):
        for f in files:
            if f.endswith(".kv"):
                yield os.path.join(root, f)


# ── the checkers themselves ─────────────────────────────────────────────────
def test_missing_token_checker_sees_a_dropped_key():
    with open(os.path.join(palettes.BUILTIN_DIR, "light.ini"), encoding="utf-8") as fh:
        full = fh.read()
    assert missing_tokens(full) == []
    dropped = re.sub(r"(?m)^text_disabled\s*=.*$", "", full)
    assert dropped != full
    assert missing_tokens(dropped) == ["[colors] text_disabled"]


def test_unknown_ref_checker_sees_a_typo():
    assert unknown_theme_refs("color: app.theme.text_disabld") == ["text_disabld"]
    assert unknown_theme_refs("color: app.theme.text[:3]") == []


def test_both_shipped_themes_are_checked():
    names = {os.path.splitext(os.path.basename(p))[0] for p in _builtin_inis()}
    assert {"dark", "light"} <= names


# ── the rules ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", _builtin_inis(), ids=os.path.basename)
def test_a_builtin_theme_defines_every_token_itself(path):
    with open(path, encoding="utf-8") as fh:
        missing = missing_tokens(fh.read())
    assert not missing, (
        f"{os.path.basename(path)} omits {missing}; the loader would backfill "
        f"them from the '{palettes.DEFAULT}' theme, i.e. another theme's colours")


def test_every_theme_key_the_kv_tree_uses_exists():
    bad = {}
    for path in _kv_files():
        with open(path, encoding="utf-8") as fh:
            unknown = unknown_theme_refs(fh.read())
        if unknown:
            bad[os.path.relpath(path, UI)] = unknown
    assert not bad, f"kv references theme keys ThemeProvider does not define: {bad}"
