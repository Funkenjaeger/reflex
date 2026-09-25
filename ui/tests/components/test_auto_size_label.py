"""The fit that keeps the status bar's version on one line (2026-09-25:
v1.2.0rc7* wrapped in the 92 dp box at 16 px on the lathe).

Only the arithmetic is tested here. The unit suite has no GL context, and
constructing a Kivy Label with text in it segfaults the process, so the
widget (AutoSizeLabel) is checked in the real app, in a window, by
ui/previews/preview_version_fit.py. Widths come from a model of the status
bar's font: ShareTech Mono is monospaced, and measured in a window it is
9.0 px per character at 16 px (v1.2.0-rc.7 = 99 px, v1.2.0 = 54 px).
"""
import pytest

from reflex.components.widgets.auto_size_button import fitted_font_size

BOX = 92          # the status bar dogleg, dp(92) at density 1


def width(text, size):
    return round(0.5625 * size * len(text))


def test_the_model_matches_the_measured_font():
    # Measured in a window, ShareTechMono-Regular.ttf, 2026-09-25.
    assert width("v1.2.0-rc.7", 16) == 99
    assert width("v1.2.0", 16) == 54
    assert width("v1.2.0rc7*", 16) == 90


@pytest.mark.parametrize("text", ["v1.2.0-rc.7", "v1.2.0-rc.12", "v1.2.10-rc.12", "v1.2.0rc7*"])
def test_a_long_version_shrinks_inside_the_box(text):
    assert width(text, 16) > BOX * 0.95, "precondition: too wide at 16"
    fs = fitted_font_size(width(text, 16), BOX, 16)
    assert fs < 16
    assert width(text, fs) <= BOX * 0.95 + 1, f"{text} at {fs:.2f}"


@pytest.mark.parametrize("text", ["v1.2.0", "v1.1.0", "v1.2.10"])
def test_a_final_release_keeps_the_full_size(text):
    assert fitted_font_size(width(text, 16), BOX, 16) == 16


def test_never_below_the_floor_and_never_above_the_max():
    assert fitted_font_size(10_000, BOX, 16) == 8
    assert fitted_font_size(1, BOX, 16) == 16
    assert fitted_font_size(0, BOX, 16) == 16        # nothing measured: leave it
    assert fitted_font_size(99, 0, 16) == 16         # no room yet (unlaid-out)
