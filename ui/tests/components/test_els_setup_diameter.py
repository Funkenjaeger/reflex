"""The "X reads diameter" toggle lives under Cross Slide Axis (X) in ELS setup.

WHY HERE AND NOT ON THE AXIS SCREEN. Radius/diameter is a property of the
cross-slide ROLE, not of axes in general -- a saddle or a spindle has no
diameter, so offering it on them would be a trap. Gating it on the axis screen
would still leave it living somewhere it does not belong, with logic deciding
when to hide it; putting it under the dropdown that nominates X removes the
question.

The VALUE is stored on the axis (an axis can be a SUM of two inputs, so a
per-input flag has no defined answer, and an input is a sensor with no opinion
about what a distance means). So this screen mirrors rather than owns it, and
the mirror has to follow the role when it is reassigned -- which is what most
of these tests are about.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _axis(name, diameter_mode=False):
    return SimpleNamespace(axis_name=name, diameter_mode=diameter_mode,
                           save_settings=lambda: None)


@pytest.fixture
def screen_factory(running_app):
    from reflex.components.screens.els_setup_screen import ElsSetupScreen

    def _make(axes, x_index=-1, z_index=-1, spindle_index=-1):
        running_app.axes = axes
        els = SimpleNamespace(x_axis_index=x_index, z_axis_index=z_index,
                              spindle_axis_index=spindle_index)
        with patch.object(ElsSetupScreen, "apply_class_lang_rules"):
            s = ElsSetupScreen()
        s.els = els
        return s

    return _make


# ── it describes whichever axis holds the X role ────────────────────────────

def test_it_reads_the_assigned_axis(screen_factory):
    axes = [_axis("Z"), _axis("X", diameter_mode=True)]
    s = screen_factory(axes, x_index=1)
    s._refresh_x_diameter()
    assert s.has_x_axis is True
    assert s.x_diameter_mode is True


def test_it_writes_to_the_assigned_axis(screen_factory):
    axes = [_axis("Z"), _axis("X")]
    s = screen_factory(axes, x_index=1)
    s._refresh_x_diameter()

    s.on_x_diameter_selected(None, True)

    assert axes[1].diameter_mode is True
    assert axes[0].diameter_mode is False, "it wrote to the wrong axis"


def test_reassigning_the_role_re_reads_the_new_axis(screen_factory):
    """THE CASE THE MIRROR EXISTS FOR. Without the re-read, reassigning X
    leaves the previous axis's setting on screen attached to a different axis
    -- and the next toggle press would write that stale value onto it."""
    axes = [_axis("A", diameter_mode=False), _axis("B", diameter_mode=True)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_diameter()
    assert s.x_diameter_mode is False

    s.on_x_selected(None, "B")

    assert s.x_diameter_mode is True, "the toggle still shows the old axis"


def test_the_previous_axis_keeps_its_own_setting(screen_factory):
    """Reassigning the role must not carry the flag across. The flag describes
    a physical mounting, not the role."""
    axes = [_axis("A", diameter_mode=True), _axis("B", diameter_mode=False)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_diameter()

    s.on_x_selected(None, "B")

    assert axes[0].diameter_mode is True
    assert axes[1].diameter_mode is False


# ── no X assigned ───────────────────────────────────────────────────────────

def test_it_collapses_with_no_x_axis(screen_factory):
    """A setting that applies to nothing is how a hidden doubling starts."""
    s = screen_factory([_axis("Z")], x_index=-1)
    s._refresh_x_diameter()
    assert s.has_x_axis is False
    assert s.x_diameter_mode is False


def test_writing_with_no_x_axis_is_a_no_op(screen_factory):
    """Guard rather than a reachable path -- the toggle is collapsed -- but a
    write with no target must not raise on a setup screen."""
    s = screen_factory([_axis("Z")], x_index=-1)
    s._refresh_x_diameter()
    s.on_x_diameter_selected(None, True)          # must not raise


def test_an_out_of_range_index_is_treated_as_unassigned(screen_factory):
    s = screen_factory([_axis("Z")], x_index=7)
    s._refresh_x_diameter()
    assert s.has_x_axis is False


# ── the kv puts it where the design says ────────────────────────────────────

def test_the_toggle_sits_under_the_cross_slide_dropdown():
    """Placement IS the design decision here, and no behavioural test can see
    it -- the widget tree is never built in this suite."""
    from pathlib import Path
    import reflex.components.screens.els_setup_screen as mod

    kv = (Path(mod.__file__).parent / "els_setup_screen.kv").read_text(encoding="utf-8")
    x_at = kv.index("Cross Slide Axis (X)")
    toggle_at = kv.index("x_diameter_toggle")
    assert toggle_at > x_at, "the toggle is not under the X dropdown"

    tail = kv[toggle_at:]
    assert "root.has_x_axis" in tail, "the toggle does not collapse without an X"


def test_it_is_NOT_offered_on_the_axis_screen():
    """The trap this design avoids: a radius/diameter control on Z and the
    spindle. The axis screen may only DISPLAY it."""
    from pathlib import Path
    import reflex.components.screens.axis_screen as mod

    kv = (Path(mod.__file__).parent / "axis_screen.kv").read_text(encoding="utf-8")
    assert "diameter_mode = " not in kv, "the axis screen can edit it"
    assert "on_value: root.axis.diameter_mode" not in kv


def test_the_axis_screen_shows_it_read_only_when_set():
    """So an axis that used to be X can never carry a doubling the axis screen
    does not admit to."""
    from pathlib import Path
    import reflex.components.screens.axis_screen as mod

    kv = (Path(mod.__file__).parent / "axis_screen.kv").read_text(encoding="utf-8")
    assert "root.axis.diameter_mode" in kv, "the axis screen never mentions it"
    assert "DIAMETER" in kv
    assert "disabled: True" in kv, "the annotation is editable"
