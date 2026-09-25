"""Which axis each scale input feeds -- the read-only join behind the input
screen annotations.

The friction this removes: inputs and axes are configured on separate screens
and nothing on the input side said what an input was for, so changing a scale
meant drilling two levels into Axes to learn which input an axis used, backing
out, and drilling into that input. Evan hit it head-on re-provisioning the X
scale on 2026-08-31 after the dial check found it wrong.

The interesting case is NOT the happy path. It is that one input can feed more
than one axis: a summed axis's second contributor is typically some other
axis's primary input, so input N legitimately belongs to both. A join that
picks one and drops the other would be wrong in exactly the configuration that
made the mapping hard to work out by hand in the first place.
"""
from types import SimpleNamespace

import pytest

from reflex.utils.input_axis_map import input_axis_label, input_axis_labels


def _axis(name, *indices, provisioned=True):
    """A stand-in carrying only what the join reads."""
    return SimpleNamespace(
        axis_name=name,
        is_provisioned=provisioned,
        transform=SimpleNamespace(input_indices=set(indices)),
    )


def test_identity_axes_map_one_to_one():
    axes = [_axis("X", 2), _axis("Z", 1), _axis("Spindle", 0)]
    assert input_axis_labels(axes) == {0: "Spindle", 1: "Z", 2: "X"}


def test_a_summed_axis_marks_both_of_its_inputs():
    """Sum membership is called out because "this input IS X" and "this input
    is half of what X is derived from" are different facts -- and only the
    second makes a lone reading of the input misleading."""
    labels = input_axis_labels([_axis("Z", 1, 3)])
    assert labels == {1: "Z (sum)", 3: "Z (sum)"}


def test_an_input_feeding_two_axes_names_both():
    """THE CASE THAT MATTERS. Input 2 is X's own input and also a contributor
    to the summed Z. Naming only one of them is the bug."""
    axes = [_axis("X", 2), _axis("Z", 2, 3)]
    labels = input_axis_labels(axes)
    assert labels[2] == "X, Z (sum)"
    assert labels[3] == "Z (sum)"


def test_unprovisioned_axes_do_not_claim_anything():
    """The board creates one axis per physical input, so a machine using three
    of four still carries the fourth. A placeholder must not look like a real
    axis here any more than it does in the axis-selection lists."""
    axes = [_axis("X", 0), _axis("?", 1, provisioned=False)]
    assert input_axis_labels(axes) == {0: "X"}


def test_an_unclaimed_input_is_absent_not_labelled():
    """Rendered blank by callers. An unused input is ordinary, not a fault to
    announce on screen."""
    labels = input_axis_labels([_axis("X", 0)])
    assert 3 not in labels
    assert input_axis_label([_axis("X", 0)], 3) == ""


def test_a_duplicate_contribution_is_not_listed_twice():
    axes = [_axis("X", 1), _axis("X", 1)]
    assert input_axis_labels(axes) == {1: "X"}


@pytest.mark.parametrize("axes", [
    None,
    [],
    [SimpleNamespace(axis_name="X", is_provisioned=True, transform=None)],
    [SimpleNamespace(axis_name="", is_provisioned=True,
                     transform=SimpleNamespace(input_indices={0}))],
    [SimpleNamespace(axis_name="X", is_provisioned=True,
                     transform=SimpleNamespace(input_indices=None))],
])
def test_malformed_input_yields_no_labels_rather_than_raising(axes):
    """This runs while a setup screen is being drawn. A half-configured axis
    must render a blank annotation, not take the screen down at the lathe."""
    assert input_axis_labels(axes) == {}


def test_single_lookup_matches_the_full_map():
    axes = [_axis("X", 2), _axis("Z", 2, 3)]
    full = input_axis_labels(axes)
    for i in range(5):
        assert input_axis_label(axes, i) == full.get(i, "")


# ── the screens actually consume it ─────────────────────────────────────────
#
# The join above is well covered, but the two lines that WIRE it into each
# screen are not reachable from a headless test (one builds Buttons in a grid,
# the other is a kv format string). Un-wiring either would leave every test
# above green and the annotation simply absent at the machine -- which is the
# whole feature.

def test_the_inputs_list_annotates_its_buttons():
    from pathlib import Path
    import reflex.components.screens.inputs_setup_screen as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "input_axis_labels" in src, "the list screen no longer builds the map"
    assert "labels.get(i" in src, "the map is built but not read per button"


def test_the_input_page_header_shows_the_axis():
    from pathlib import Path
    import reflex.components.screens.input_screen as mod

    py = Path(mod.__file__).read_text(encoding="utf-8")
    kv = (Path(mod.__file__).parent / "input_screen.kv").read_text(encoding="utf-8")
    assert "input_axis_label" in py
    assert "root.axis_label" in kv, "the header no longer renders the annotation"


def test_the_join_never_writes_to_an_axis():
    """The constraint, not a phase. Evan: 'I'm reluctant to create multiple
    disjoint ways of doing the same thing... just read-only there for now.'

    Checked by watching for writes rather than by grepping the source: a
    string search for "transform =" matches a local binding as readily as a
    mutation, which is the bare-phrase trap. This records every setattr on the
    axes handed in, so any assignment fails with the attribute named.
    """
    written = []

    class _Watched:
        def __init__(self, name, *indices):
            object.__setattr__(self, "axis_name", name)
            object.__setattr__(self, "is_provisioned", True)
            object.__setattr__(
                self, "transform",
                SimpleNamespace(input_indices=set(indices)))

        def __setattr__(self, key, value):
            written.append(key)
            object.__setattr__(self, key, value)

    axes = [_Watched("X", 2), _Watched("Z", 2, 3)]
    assert input_axis_labels(axes)[2] == "X, Z (sum)"
    assert written == [], f"the join wrote to an axis: {written}"


def test_the_input_header_is_not_an_editor():
    """The annotation renders the label; it must not offer to set it."""
    from pathlib import Path
    import reflex.components.screens.input_screen as mod

    kv = (Path(mod.__file__).parent / "input_screen.kv").read_text(encoding="utf-8")
    assert "root.axis_label" in kv
    assert "axis_label =" not in kv, "the header became editable"
