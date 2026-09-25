"""The "X DRO reads" choice lives under Cross Slide Axis (X) in ELS setup.

WHY HERE AND NOT ON THE AXIS SCREEN. Radius/diameter is a property of the
cross-slide ROLE, not of axes in general -- a saddle or a spindle has no
diameter, so offering it on them would be a trap. Gating it on the axis screen
would still leave it living somewhere it does not belong, with logic deciding
when to hide it; putting it under the dropdown that nominates X removes the
question.

WHY A DROPDOWN OF TWO NAMED CONVENTIONS rather than a boolean. It shipped on
2026-09-01 as an on/off "X reads diameter", where OFF has to be read as "reads
radius instead" -- an inference only someone already fluent in the setting
makes, and a dimmed OFF is exactly where it gets skipped. Naming both
alternatives removes the inference.

The VALUE is stored on the axis, and stored as the BOOLEAN `diameter_mode` (an
axis can be a SUM of two inputs, so a per-input flag has no defined answer, and
an input is a sensor with no opinion about what a distance means). The labels
are the UI's vocabulary, not the config's -- machines already carrying
`diameter_mode: true` keep working untouched. So this screen mirrors rather
than owns the value, and the mirror has to follow the role when it is
reassigned, which is what most of these tests are about.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from reflex.components.screens.els_setup_screen import (DIAMETER_LABEL,
                                                        DRO_READS_OPTIONS,
                                                        RADIUS_LABEL)


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


# ── both conventions are named ──────────────────────────────────────────────

def test_both_options_are_spelled_out():
    """The whole point of the rework: neither convention is implied by the
    absence of the other."""
    assert DRO_READS_OPTIONS == [RADIUS_LABEL, DIAMETER_LABEL]
    assert RADIUS_LABEL and DIAMETER_LABEL


# ── it describes whichever axis holds the X role ────────────────────────────

def test_it_reads_the_assigned_axis(screen_factory):
    axes = [_axis("Z"), _axis("X", diameter_mode=True)]
    s = screen_factory(axes, x_index=1)
    s._refresh_x_dro_reads()
    assert s.has_x_axis is True
    assert s.x_dro_reads == DIAMETER_LABEL


def test_radius_is_the_default_reading(screen_factory):
    axes = [_axis("Z"), _axis("X", diameter_mode=False)]
    s = screen_factory(axes, x_index=1)
    s._refresh_x_dro_reads()
    assert s.x_dro_reads == RADIUS_LABEL


def test_it_writes_to_the_assigned_axis(screen_factory):
    axes = [_axis("Z"), _axis("X")]
    s = screen_factory(axes, x_index=1)
    s._refresh_x_dro_reads()

    s.on_x_dro_reads_selected(None, DIAMETER_LABEL)

    assert axes[1].diameter_mode is True
    assert axes[0].diameter_mode is False, "it wrote to the wrong axis"


def test_choosing_radius_clears_it(screen_factory):
    """The half a boolean made unsayable: going back is an explicit choice of
    the other named convention, not the absence of this one."""
    axes = [_axis("X", diameter_mode=True)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_dro_reads()

    s.on_x_dro_reads_selected(None, RADIUS_LABEL)

    assert axes[0].diameter_mode is False
    assert s.x_dro_reads == RADIUS_LABEL


def test_reassigning_the_role_re_reads_the_new_axis(screen_factory):
    """THE CASE THE MIRROR EXISTS FOR. Without the re-read, reassigning X
    leaves the previous axis's setting on screen attached to a different axis
    -- and the next selection would write that stale value onto it."""
    axes = [_axis("A", diameter_mode=False), _axis("B", diameter_mode=True)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_dro_reads()
    assert s.x_dro_reads == RADIUS_LABEL

    s.on_x_selected(None, "B")

    assert s.x_dro_reads == DIAMETER_LABEL, "it still shows the old axis"


def test_the_previous_axis_keeps_its_own_setting(screen_factory):
    """Reassigning the role must not carry the flag across. The flag describes
    a physical mounting, not the role."""
    axes = [_axis("A", diameter_mode=True), _axis("B", diameter_mode=False)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_dro_reads()

    s.on_x_selected(None, "B")

    assert axes[0].diameter_mode is True
    assert axes[1].diameter_mode is False


# ── an unrecognized label must not be read as radius ────────────────────────

def test_an_empty_label_is_ignored(screen_factory):
    """DropDownItem.value is a free StringProperty that starts EMPTY, so the kv
    binding can hand us "" during construction. Reading anything-not-Diameter
    as radius would let that quietly halve a diameter machine's readout -- the
    exact silent-factor-of-two class this whole feature exists to end."""
    axes = [_axis("X", diameter_mode=True)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_dro_reads()

    s.on_x_dro_reads_selected(None, "")

    assert axes[0].diameter_mode is True, "an empty label cleared the setting"
    assert s.x_dro_reads == DIAMETER_LABEL


def test_an_unknown_label_is_ignored(screen_factory):
    axes = [_axis("X", diameter_mode=True)]
    s = screen_factory(axes, x_index=0)
    s._refresh_x_dro_reads()

    s.on_x_dro_reads_selected(None, "Nonsense")

    assert axes[0].diameter_mode is True
    assert s.x_dro_reads == DIAMETER_LABEL


# ── no X assigned ───────────────────────────────────────────────────────────

def test_it_collapses_with_no_x_axis(screen_factory):
    """A setting that applies to nothing is how a hidden doubling starts."""
    s = screen_factory([_axis("Z")], x_index=-1)
    s._refresh_x_dro_reads()
    assert s.has_x_axis is False
    assert s.x_dro_reads == RADIUS_LABEL


def test_writing_with_no_x_axis_is_a_no_op(screen_factory):
    """Guard rather than a reachable path -- the row is collapsed -- but a
    write with no target must not raise on a setup screen."""
    s = screen_factory([_axis("Z")], x_index=-1)
    s._refresh_x_dro_reads()
    s.on_x_dro_reads_selected(None, DIAMETER_LABEL)    # must not raise


def test_an_out_of_range_index_is_treated_as_unassigned(screen_factory):
    s = screen_factory([_axis("Z")], x_index=7)
    s._refresh_x_dro_reads()
    assert s.has_x_axis is False


# ── the kv puts it where the design says ────────────────────────────────────

def test_the_choice_sits_under_the_cross_slide_dropdown():
    """Placement IS the design decision here, and no behavioural test can see
    it -- the widget tree is never built in this suite."""
    from pathlib import Path
    import reflex.components.screens.els_setup_screen as mod

    kv = (Path(mod.__file__).parent / "els_setup_screen.kv").read_text(encoding="utf-8")
    x_at = kv.index("Cross Slide Axis (X)")
    row_at = kv.index("x_dro_reads_dropdown")
    assert row_at > x_at, "the row is not under the X dropdown"

    tail = kv[row_at:]
    assert "root.has_x_axis" in tail, "the row does not collapse without an X"


def test_the_kv_offers_the_two_named_options():
    """It must be a two-way choice on screen, not a boolean. A kv that fell
    back to a BooleanItem would pass every behavioural test above."""
    from pathlib import Path
    import reflex.components.screens.els_setup_screen as mod

    kv = (Path(mod.__file__).parent / "els_setup_screen.kv").read_text(encoding="utf-8")
    row_at = kv.index("x_dro_reads_dropdown")
    widget = next(ln.strip() for ln in reversed(kv[:row_at].splitlines())
                  if ln.strip().endswith("Item:"))
    assert widget == "DropDownItem:", f"the row is a {widget} not a dropdown"


def test_the_kv_does_NOT_bind_the_value():
    """THE HAZARD THIS SHAPE AVOIDS, and it nearly shipped. A kv `value:` is
    applied while the tree is built -- before on_pre_enter, before `els` is
    set -- so it fires on_value with the property's DEFAULT and writes "Radius"
    onto an axis provisioned for diameter. The readout halves at startup, with
    no operator action and nothing in the log. The value is pushed in from
    _refresh_x_dro_reads instead, exactly as the three dropdowns above do it.
    """
    from pathlib import Path
    import reflex.components.screens.els_setup_screen as mod

    kv = (Path(mod.__file__).parent / "els_setup_screen.kv").read_text(encoding="utf-8")
    row = kv[kv.index("x_dro_reads_dropdown"):]
    row = row[:row.index("\n\n")] if "\n\n" in row else row
    body = [ln.strip() for ln in row.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    assert not any(ln.startswith("value:") for ln in body), \
        "the row kv-binds `value:` -- it will write the default at build time"
    assert any(ln.startswith("on_value:") for ln in body), \
        "the row no longer reports selections at all"


def test_refreshing_pushes_options_and_value_into_the_row(screen_factory):
    """The other half of not binding it: the mirror has to actually reach the
    widget, or the dropdown opens empty and shows nothing."""
    axes = [_axis("X", diameter_mode=True)]
    s = screen_factory(axes, x_index=0)
    row = SimpleNamespace(options=[], value="")
    s.ids["x_dro_reads_dropdown"] = row

    s._refresh_x_dro_reads()

    assert list(row.options) == DRO_READS_OPTIONS
    assert row.value == DIAMETER_LABEL


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
