"""The safe diameter: a committed value for stop + retract, so the X-clear
gate stops being vacuous there.

THE DEFECT. Pressing Retract feeds the carriage back to Start Z under power,
and a tool still in the groove is dragged along the thread. Wizard mode
already gates that button on ``_x_clear_of_start_dia()`` -- but that predicate
returns True whenever no diameter is committed, and until now ONLY the wizard
could commit one. So in stop + retract the gate was vacuously satisfied and
caught nothing. Evan: "that's the main reason I don't often use the mode."

NO NEW MECHANISM WAS ADDED. The predicate, the refusal message and the disable
path all already existed and are already exercised in wizard mode; the gate at
ui_controller.py keys on ``is_threading``, not on wizard. All that was missing
was a way to put a value in. That is why these tests are about VISIBILITY and
NAMING rather than about gating behaviour -- the gating is somebody else's
tested code and this change does not touch it.

WHAT IS DELIBERATELY NOT TESTED HERE: that an uncommitted diameter leaves
Retract ungated. That is pre-existing behaviour of _x_clear_of_start_dia and
its own docstring records why (an earlier attempt at making the gate live
without a committed value blocked EVERY threading retract on a machine whose X
DRO sits below its power-on zero). Evan re-confirmed keeping it 2026-08-31.
Asserting it here would be claiming credit for a guard this change left alone.
"""
import pytest


# ── when the field is on screen ─────────────────────────────────────────────
#
# Truth table, and each row is a decision rather than an example:
#   wizard              -> always. It is the MAJOR diameter there, collected as
#                          wizard step 3.
#   stop + retract, on  -> the point of the feature.
#   stop + retract, off -> optional, and Evan chose "hidden" over "greyed".
#   stop-only           -> never. No Retract in that mode, so no hazard to gate
#                          and nothing for the value to do.

@pytest.mark.parametrize("wizard,retract,safe,expected", [
    (True,  True,  True,  True),
    (True,  True,  False, True),   # wizard needs it regardless of the setting
    (True,  False, False, True),
    (False, True,  True,  True),   # <- the case this feature exists for
    (False, True,  False, False),  # optional, and off means HIDDEN
    (False, False, True,  False),  # stop-only: no Retract, no gate, no field
    (False, False, False, False),
])
def test_when_the_diameter_field_is_shown(advbar_factory, running_app,
                                          wizard, retract, safe, expected):
    bar = advbar_factory(els_bar=None, enable_wizard=wizard,
                         enable_retract=retract, enable_safe_dia=safe)
    assert bar.show_start_dia is expected


def test_it_is_on_by_default(advbar_factory, running_app):
    """Evan's call: optional, but enabled by default. The gate it turns on is
    the only thing between Retract and a tool dragged back through the thread,
    so the default is the protective one."""
    bar = advbar_factory(els_bar=None)
    assert bar.enable_safe_dia is True


def test_the_setting_cannot_hide_it_from_the_wizard(advbar_factory, running_app):
    """A negative control on the setting's reach. In wizard mode the field is
    the thread's major diameter and the wizard's own step 3 collects it --
    turning the stop+retract convenience off must not break that."""
    bar = advbar_factory(els_bar=None, enable_wizard=True, enable_safe_dia=False)
    assert bar.show_start_dia is True


def test_visibility_updates_live_when_the_setting_changes(advbar_factory, running_app):
    """It is an AliasProperty with an explicit bind list; a missing entry there
    would leave the kv showing a stale field until something else redrew."""
    bar = advbar_factory(els_bar=None, enable_wizard=False, enable_retract=True)
    seen = []
    bar.bind(show_start_dia=lambda _i, v: seen.append(v))

    bar.enable_safe_dia = False
    bar.enable_safe_dia = True

    assert seen == [False, True], "show_start_dia did not re-derive on change"


# ── what it is called ───────────────────────────────────────────────────────

def test_the_label_follows_the_job_not_the_storage(advbar_factory, running_app):
    """Same stored value (controller.start_dia), two names.

    "Major ø" is thread geometry and is correct in the wizard. In stop +
    retract nothing is being threaded to a major diameter, so naming a
    clearance gate after thread geometry is actively misleading -- which is
    what the roadmap's framing missed: it posed the choice as "safe ø versus
    reusing START ø", but the button on the bar has always read "Major ø".
    """
    wiz = advbar_factory(els_bar=None, enable_wizard=True)
    ret = advbar_factory(els_bar=None, enable_wizard=False, enable_retract=True)

    assert wiz.start_dia_label == "Major ø"
    assert ret.start_dia_label == "Safe ø"


def test_the_label_updates_live_with_the_mode(advbar_factory, running_app):
    bar = advbar_factory(els_bar=None, enable_wizard=True)
    seen = []
    bar.bind(start_dia_label=lambda _i, v: seen.append(v))

    bar.enable_wizard = False

    assert seen == ["Safe ø"]


# ── the kv actually consumes them ───────────────────────────────────────────

def test_the_kv_uses_the_derived_properties(advbar_factory, running_app):
    """Guards the reason they are derived at all.

    The condition used to appear three times in the kv (width, opacity,
    disabled). Anyone re-inlining `root.enable_wizard` on one of those lines
    would reintroduce exactly the drift these properties exist to prevent, and
    no behavioural test would notice because the widget tree is never built in
    this suite.
    """
    from pathlib import Path
    import re
    import reflex.components.home.els_advbar as mod

    kv = (Path(mod.__file__).parent / "els_advbar.kv").read_text(encoding="utf-8")
    block = kv[kv.index("id: btn_major_dia"):]
    block = block[:block.index("id: btn_minor_dia")]

    assert block.count("root.show_start_dia") == 3, \
        "width, opacity and disabled must all key off show_start_dia"
    assert "root.start_dia_label" in block
    assert not re.search(r"root\.enable_wizard", block), \
        "the raw flag is back in the diameter button -- that is the drift"


# ── an uncommitted field must not display a value (Evan, 2026-09-01) ────────

def test_the_diameter_buttons_show_dashes_until_committed():
    """0.000 IS A REAL DIAMETER depending on how X was referenced, so it is not
    an "obviously unset" placeholder. Displaying it for an uncommitted field
    claims a value that does not exist -- and for Safe ø the claim is that the
    Retract gate is armed when it is not.

    Stop Z and Start Z have always done this; the two diameter buttons never
    did, which only became visible once the field appeared outside the wizard.
    """
    from pathlib import Path
    import reflex.components.home.els_advbar as mod

    kv = (Path(mod.__file__).parent / "els_advbar.kv").read_text(encoding="utf-8")

    for btn, valid in (("btn_major_dia", "start_dia_valid"),
                       ("btn_minor_dia", "stop_dia_valid")):
        block = kv[kv.index(f"id: {btn}"):]
        block = block[:block.index("on_long_press")]
        assert f"root.controller.{valid}" in block, \
            f"{btn} renders a value with no committed check"
        assert 'else "--"' in block, f"{btn} has no unset rendering"


def test_the_keypad_title_follows_the_field_name(advbar_factory, running_app):
    """Long-pressing the field in stop + retract opened a keypad headed
    "Major ø" -- naming a thread dimension in a mode where nothing is being
    threaded to one."""
    from pathlib import Path
    import reflex.components.home.els_advbar as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'title_label = (self.start_dia_label if which == "major"' in src, \
        "the keypad title is hardcoded again"

    ret = advbar_factory(els_bar=None, enable_wizard=False, enable_retract=True)
    assert ret.start_dia_label == "Safe ø"
