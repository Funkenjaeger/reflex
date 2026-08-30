"""Every shipped theme must keep a disabled label readable.

Measured on the screenshots 2026-08-30, a disabled "Cut" scored 1.19:1 against
its own card in light and 2.13:1 in dark, where WCAG's floor for large text is
3:1 -- i.e. the control was on screen saying what the button would do, and
could not be read. Two causes, both fixed:

  * StyledButton dimmed the WHOLE widget with `opacity`, which pulls the label
    and the fill toward the page from opposite sides until they meet. No
    opacity value fixes that; it is the wrong axis. It dims by colour now.
  * Kivy's Label renders `disabled_color`, not `color`, while disabled -- and
    `disabled` propagates from the button down to it. Setting `color` alone
    changed nothing in exactly the state being fixed.

This file guards the PALETTE half, because that is the half a future colour
edit can silently undo. The rendering half is guarded by the fact that the two
properties are set together in styled_button.kv.

`recess` is the fill of a blank action button -- the worst case, because it is
the closest fill to the page behind it.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import pytest

from reflex.components.widgets.palettes import PALETTES

# WCAG 2.1: 3:1 for large text (the button labels are 20 px and bold), 4.5:1
# for body text. A button label is large text.
LARGE_TEXT_FLOOR = 3.0


def _lin(c):
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgba):
    r, g, b = (_lin(max(0.0, min(1.0, v))) for v in rgba[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a) + 0.05, luminance(b) + 0.05
    return max(la, lb) / min(la, lb)


def test_the_metric_itself_is_right():
    """Black on white is 21:1 and a colour against itself is 1:1.

    Here because the first two attempts at this measurement were both wrong --
    once averaging raw sRGB instead of linearising, once sampling an
    antialiased edge instead of a stroke core -- and a contrast check that is
    quietly computing the wrong number is worse than none.
    """
    assert round(contrast((0, 0, 0, 1), (1, 1, 1, 1)), 1) == 21.0
    assert contrast((0.3, 0.4, 0.5, 1), (0.3, 0.4, 0.5, 1)) == 1.0


@pytest.mark.parametrize("theme", sorted(PALETTES))
def test_a_disabled_label_is_readable_on_a_blank_action_button(theme):
    p = PALETTES[theme]
    got = contrast(p["text_disabled"], p["recess"])
    assert got >= LARGE_TEXT_FLOOR, (
        f"{theme}: a disabled button label scores {got:.2f}:1 against the "
        f"recessed fill of a blank action button, under the {LARGE_TEXT_FLOOR}:1 "
        f"floor for large text. A disabled control is on screen precisely to "
        f"say what the button WOULD do; if it cannot be read, hide it with "
        f"`hidden` instead.")


@pytest.mark.parametrize("theme", sorted(PALETTES))
def test_disabled_still_reads_as_subordinate_to_enabled(theme):
    """Readable is not the only requirement -- it must still look disabled."""
    p = PALETTES[theme]
    disabled = contrast(p["text_disabled"], p["recess"])
    enabled = contrast(p["text"], p["recess"])
    assert enabled > disabled * 1.5, (
        f"{theme}: enabled text scores {enabled:.2f}:1 and disabled "
        f"{disabled:.2f}:1 against the same fill. Too close -- a disabled "
        f"control that reads as loudly as a live one invites a press.")


@pytest.mark.parametrize("theme", sorted(PALETTES))
def test_enabled_text_clears_the_body_floor_on_both_fills(theme):
    """The enabled case, which was never in doubt and is cheap to pin."""
    p = PALETTES[theme]
    for fill in ("surface", "recess"):
        got = contrast(p["text"], p[fill])
        assert got >= 4.5, (
            f"{theme}: enabled text on {fill} is {got:.2f}:1, under the 4.5:1 "
            f"body-text floor")
