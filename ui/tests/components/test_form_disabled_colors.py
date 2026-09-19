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
    <Form...> rule (children's properties are indented deeper and skipped)."""
    rules, name = {}, None
    head = re.compile(r"^<(Form\w+)(?:@[\w+]+)?>:\s*$")
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
    for expected in ("FormLabel", "FormValueButton", "FormToggle", "FormTextInput"):
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
