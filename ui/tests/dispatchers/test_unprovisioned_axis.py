"""An axis nobody has named must not be offered as a summed contributor.

The board creates one axis per physical scale input, so a lathe using three of
four inputs carries a fourth axis named "?" -- the class default, and the only
"not provisioned yet" marker the system has. That is CORRECT: the input exists
and may be wired later, and deleting the axis to tidy up would throw away a
real port.

What it must not do is look like a real axis to anything offering choices.
Axis.compute() adds both contributors of a SUM into the displayed position,
while consumers that push a single scale index to the firmware --
ElsFsm.set_scale_index above all -- take contributions[0] alone. Summing in a
placeholder that reads zero forever is a DRO disagreeing with the machine for
no gain.
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from types import SimpleNamespace

import pytest

from reflex.dispatchers.axis import AxisDispatcher
from reflex.dispatchers.axis_transform import AxisTransform


# ── the marker ──────────────────────────────────────────────────────────

def test_a_fresh_axis_is_unprovisioned():
    """The default name IS the marker; nothing else records the state."""
    assert AxisDispatcher.UNPROVISIONED_NAME == "?"


@pytest.mark.parametrize("name,provisioned", [
    ("?", False),
    ("", False),
    ("Z", True),
    ("X", True),
    ("compound", True),
    ("?axis", True),   # only the bare marker counts, not anything containing it
])
def test_is_provisioned_reads_the_name(name, provisioned):
    stub = SimpleNamespace(axis_name=name,
                           UNPROVISIONED_NAME=AxisDispatcher.UNPROVISIONED_NAME)
    assert AxisDispatcher.is_provisioned.fget(stub) is provisioned


# ── the screen ──────────────────────────────────────────────────────────

def _screen(axes, editing, transform_label="Sum", input_0="Input 1",
            input_1="Input 3", n_inputs=4, spindle=(0,)):
    """AxisScreen's two filtering methods, without a running app.

    AxisScreen.__init__ reaches for MainApp.get_running_app(), so the methods
    are called unbound against a stand-in carrying only what they read.
    """
    from reflex.components.screens.axis_screen import AxisScreen
    scr = SimpleNamespace(
        axis=editing,
        transform_type_label=transform_label,
        input_0=input_0,
        input_1=input_1,
        input_0_options=[f"Input {i}" for i in range(n_inputs) if i not in spindle],
        input_1_options=[],
        app=SimpleNamespace(
            axes=axes,
            inputs=[SimpleNamespace(spindleMode=(i in spindle))
                    for i in range(n_inputs)]),
    )
    scr._provisioned_input_labels = lambda: AxisScreen._provisioned_input_labels(scr)
    scr._all_input_labels = lambda: AxisScreen._all_input_labels(scr)
    return AxisScreen, scr


def _axis(name, *inputs):
    t = (AxisTransform.identity(inputs[0]) if len(inputs) == 1
         else AxisTransform.sum(*inputs))
    return SimpleNamespace(axis_name=name, transform=t,
                           is_provisioned=(name != "?" and bool(name)),
                           spindleMode=False)


def test_an_unnamed_axis_input_is_not_offered_as_a_second_contributor():
    """THE CASE THAT PROMPTED THIS. elspi: X on input 2, Z on input 1, spindle
    on input 0, and a "?" placeholder on input 3. Editing Z, the only thing
    worth summing with is X."""
    z = _axis("Z", 1)
    axes = [_axis("S", 0), z, _axis("X", 2), _axis("?", 3)]
    AxisScreen, scr = _screen(axes, editing=z)
    AxisScreen._update_input_options(scr)
    assert scr.input_1_options == ["Input 2"], (
        f"input 3 belongs to an axis nobody has named; got {scr.input_1_options}")


def test_naming_the_axis_makes_its_input_eligible():
    """The placeholder is not banned, it is UNPROVISIONED. Provision it and it
    counts -- which is the whole reason the axis is kept rather than deleted."""
    z = _axis("Z", 1)
    axes = [_axis("S", 0), z, _axis("X", 2), _axis("Y", 3)]
    AxisScreen, scr = _screen(axes, editing=z)
    AxisScreen._update_input_options(scr)
    assert scr.input_1_options == ["Input 2", "Input 3"]


def test_the_stale_selection_is_moved_to_something_eligible():
    z = _axis("Z", 1)
    axes = [_axis("S", 0), z, _axis("X", 2), _axis("?", 3)]
    AxisScreen, scr = _screen(axes, editing=z, input_1="Input 3")
    AxisScreen._update_input_options(scr)
    assert scr.input_1 == "Input 2"


def test_identity_is_not_narrowed():
    """input_0 must stay wide: assigning an input to an axis is HOW an axis
    gets provisioned, so narrowing it would strand every fresh axis."""
    z = _axis("Z", 1)
    axes = [_axis("S", 0), z, _axis("X", 2), _axis("?", 3)]
    AxisScreen, scr = _screen(axes, editing=z, transform_label="Identity")
    AxisScreen._update_input_options(scr)
    assert scr.input_1_options == scr.input_0_options
    assert "Input 3" in scr.input_0_options


def test_editing_the_placeholder_does_not_make_its_own_input_eligible():
    q = _axis("?", 3)
    axes = [_axis("S", 0), _axis("Z", 1), q]
    AxisScreen, scr = _screen(axes, editing=q, input_0="Input 3")
    AxisScreen._update_input_options(scr)
    assert "Input 3" not in scr.input_1_options


def test_an_axis_cannot_vouch_for_its_own_stale_contributor():
    """A SUM stored before this rule existed must not justify itself.

    Z is saved as sum(1, 3) and nothing else names input 3 -- the placeholder
    on it is still "?". Editing Z, input 3 must NOT come back as eligible on
    the strength of Z's own stored transform, or the narrowing never happens
    for exactly the configurations that need it.

    THE EARLIER VERSION OF THIS TEST COULD NOT FAIL: it edited the
    unprovisioned axis, which `not ax.is_provisioned` already excluded, so
    deleting the self-skip changed nothing and the mutation ran green.
    """
    z = _axis("Z", 1, 3)
    axes = [_axis("S", 0), z, _axis("?", 2), _axis("?", 3)]
    AxisScreen, scr = _screen(axes, editing=z, input_0="Input 1",
                              input_1="Input 3")
    AxisScreen._update_input_options(scr)
    assert "Input 3" not in scr.input_1_options, (
        f"Z is vouching for its own stored contributor; got "
        f"{scr.input_1_options}")


def test_nothing_eligible_leaves_no_options_rather_than_a_wrong_one():
    """A machine with one provisioned axis has nothing to sum with. Empty is
    the honest answer; apply_transform then refuses (see below)."""
    z = _axis("Z", 1)
    axes = [_axis("S", 0), z, _axis("?", 2), _axis("?", 3)]
    AxisScreen, scr = _screen(axes, editing=z)
    AxisScreen._update_input_options(scr)
    assert scr.input_1_options == []
    assert scr.input_1 == "Input 3", "a stale value is left alone, not invented"
