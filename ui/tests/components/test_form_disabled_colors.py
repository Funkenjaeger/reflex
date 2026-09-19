"""The shared settings-row controls theme their DISABLED colours, not just `color`.

Reported at the lathe 2026-09-19: most entries on Setup > System were
invisible in the dark theme. Those rows are StringItems with `disabled: True`
(read-only values), and a disabled Kivy TextInput draws its text in
`disabled_foreground_color` -- stock value black at 50% -- on FormTextInput's
dark `recess` fill: 1.02:1. The disabled "Resize Partition" row did the same
thing with Label's stock `disabled_color` (white at 30%). Every Form* rule in
facelift_chrome.kv set its enabled colour from app.theme and left the disabled
one at Kivy's default, from the day they were introduced (02def60).

This pins the rule shape: a Form* control that themes an enabled text colour
must theme the disabled twin too. The pixels are measured by
previews/preview_setup_contrast.py; the palette values by test_theme_contrast.py.
"""
import os
import re

import pytest

import reflex

KV = os.path.join(os.path.dirname(reflex.__file__),
                  "components", "widgets", "facelift_chrome.kv")

# enabled text colour property -> the property Kivy draws while disabled
TWINS = {"color": "disabled_color", "foreground_color": "disabled_foreground_color"}


def _rules():
    """{rule name: {property: value}} for the top-level properties of each
    shared <Form...>, <Setup...> and <Themed...> rule (children's properties
    are indented deeper and skipped)."""
    rules, name = {}, None
    head = re.compile(r"^<((?:Form|Setup|Themed)\w+)(?:@[\w+]+)?>:\s*$")
    prop = re.compile(r"^    (\w+):\s*(.+?)\s*$")
    with open(KV, encoding="utf-8") as fh:
        for line in fh:
            m = head.match(line)
            if m:
                name = m.group(1)
                rules[name] = {}
                continue
            if line.startswith("<"):
                name = None
                continue
            if name is None:
                continue
            m = prop.match(line)
            if m:
                rules[name][m.group(1)] = m.group(2)
    return rules


RULES = _rules()


def test_the_parser_found_the_form_controls():
    """A parser that finds nothing makes every check below pass vacuously."""
    for expected in ("FormLabel", "FormValueButton", "FormToggle", "FormTextInput",
                     "SetupButton"):
        assert expected in RULES, f"{expected} rule not found in {KV}"
    assert RULES["FormLabel"].get("color") == "app.theme.text"


@pytest.mark.parametrize("rule", sorted(RULES))
def test_a_themed_text_colour_has_a_themed_disabled_twin(rule):
    props = RULES[rule]
    for enabled, disabled in TWINS.items():
        if enabled not in props:
            continue
        got = props.get(disabled)
        assert got is not None and "app.theme." in got, (
            f"<{rule}> sets {enabled} from the theme but leaves {disabled} at "
            f"Kivy's stock value ({disabled}={got!r}); a disabled {rule} then "
            f"draws in a colour the theme never chose -- black-at-50% on the "
            f"dark recess fill is 1.02:1, i.e. invisible.")


def test_a_disabled_text_input_keeps_the_themed_well():
    """Without background_disabled_normal='' a disabled FormTextInput swaps to
    Kivy's stock textinput_disabled image, so the well it is measured against
    is not the one the theme defines."""
    assert RULES["FormTextInput"].get("background_disabled_normal") == "''"


# ── No rule dims text by alpha ──────────────────────────────────────────────
# <SetupButton> drew its disabled label as `app.theme.text[:3] + [0.35]` --
# the enabled ink at 35% alpha. Alpha blends the ink toward whatever fill is
# behind it, so the result is set by the FILL, not the theme: 2.07:1 dark and
# 1.98:1 light on Backup's disabled gist buttons (2026-09-19). A theme's
# text_disabled is tuned and tested for >= 3:1 on the fills (see
# test_theme_contrast.py); an alpha-dimmed colour is not tested anywhere.
REFLEX_DIR = os.path.dirname(reflex.__file__)
TEXT_COLOUR = re.compile(
    r"^\s*(color|disabled_color|foreground_color|disabled_foreground_color"
    r"|title_color|hint_text_color):\s*(.+?)\s*$")
# a colour spliced from a theme colour's rgb plus a hand-picked alpha
ALPHA_SPLICE = re.compile(r"\[\s*:\s*3\s*\]\s*\+\s*[\[(]")


def _kv_files():
    for root, _dirs, files in os.walk(REFLEX_DIR):
        for f in files:
            if f.endswith(".kv"):
                yield os.path.join(root, f)


def _alpha_dimmed_text_colours():
    hits = []
    for path in _kv_files():
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                m = TEXT_COLOUR.match(line)
                if m and ALPHA_SPLICE.search(m.group(2)):
                    hits.append(f"{os.path.relpath(path, REFLEX_DIR)}:{n}: {line.strip()}")
    return hits


def test_the_alpha_splice_pattern_matches_the_shape_it_guards_against():
    """Seen-red for the scan below, on the exact line that shipped."""
    m = TEXT_COLOUR.match("  disabled_color: app.theme.text[:3] + [0.35]")
    assert m and ALPHA_SPLICE.search(m.group(2))
    m = TEXT_COLOUR.match("    color: app.theme.text")
    assert m and not ALPHA_SPLICE.search(m.group(2))
    assert len(list(_kv_files())) > 20, "kv scan found almost no files"


def test_no_kv_rule_dims_text_by_alpha():
    hits = _alpha_dimmed_text_colours()
    assert not hits, (
        "text colour made by splicing an alpha onto a theme colour -- the "
        "contrast then depends on the fill behind it and no palette test "
        "covers it. Use app.theme.text_disabled (or another themed key):\n  "
        + "\n  ".join(hits))


def test_setup_button_is_defined_once():
    """Two <SetupButton> rules would silently merge, and the later file's
    disabled colour would win or lose by load order."""
    found = []
    for path in _kv_files():
        with open(path, encoding="utf-8") as fh:
            if any(line.startswith("<SetupButton") for line in fh):
                found.append(os.path.relpath(path, REFLEX_DIR))
    assert found == [os.path.join("components", "widgets", "facelift_chrome.kv")], found
