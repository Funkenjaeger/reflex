"""Every ELS-bar illustration the machine can ask for must exist on disk.

THE DEFECT THIS EXISTS FOR (2026-08-30). The thread-hand illustration carries a
baked-in RH/LH label that was pure yellow in both themes and unreadable on the
light one, so it gained `_dark` and `_light` variants. The kv then appended a
theme suffix to EVERY move_type -- but move_type has four values, and only the
threading pair has variants. In feed mode the widget asked for
`turn_in_dark.png`, which does not exist, and drew a white rectangle across the
bar where the illustration belongs.

Nothing caught it because nothing rendered feed mode. The screenshot that found
it was added minutes later, for unrelated reasons.

A missing image is a silent failure in Kivy: no exception, no log line, just a
blank texture. So the check has to be "does the file exist", asked of every
value the property can return -- not "does the app start".
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import pytest

import reflex
from reflex.components.home.elsbar import _ElsBarMoveTypes, move_image

PICTURES = os.path.dirname(reflex.__file__)

# A dark ground and a light one, by luminance rather than by theme name --
# palettes.py loads user themes, so there are not only two.
GROUNDS = {
    "dark": [0.055, 0.067, 0.075, 1],
    "light": [0.851, 0.863, 0.871, 1],
    "unset": None,
}


@pytest.mark.parametrize("move_type", _ElsBarMoveTypes.ALL)
@pytest.mark.parametrize("ground", sorted(GROUNDS))
def test_every_illustration_resolves_to_a_real_file(move_type, ground):
    rel = move_image(move_type, GROUNDS[ground])
    path = os.path.join(PICTURES, rel)
    assert os.path.exists(path), (
        f"{move_type} on a {ground} ground resolves to {rel}, which does not "
        f"exist. Kivy renders a missing image as a blank texture with no error "
        f"-- the operator sees a white rectangle where the illustration should "
        f"be.")


@pytest.mark.parametrize("move_type", ["thread_rh", "thread_lh"])
def test_the_threading_pair_follows_the_theme(move_type):
    """The whole reason the variants exist: the baked-in RH/LH label."""
    dark = move_image(move_type, GROUNDS["dark"])
    light = move_image(move_type, GROUNDS["light"])
    assert dark != light
    assert dark.endswith("_dark.png") and light.endswith("_light.png")


@pytest.mark.parametrize("move_type", ["turn_in", "turn_out"])
def test_the_turning_pair_does_not(move_type):
    """They carry no text, so there is nothing to recolour and no variant to
    ask for. Appending a suffix here is exactly the bug."""
    same = {move_image(move_type, g) for g in GROUNDS.values()}
    assert same == {f"pictures/{move_type}.png"}, (
        f"turning illustrations must not vary by theme; got {same}")


def test_the_roster_matches_what_the_property_can_return():
    """A fifth illustration must be registered, or this file silently stops
    covering it."""
    import inspect

    from reflex.components.home.elsbar import ElsBar

    src = inspect.getsource(ElsBar._get_move_type)
    for name in _ElsBarMoveTypes.ALL:
        assert f'"{name}"' in src, (
            f"{name} is in the roster but _get_move_type cannot return it")
    import re

    # Every quoted lowercase token in the method: in this one they are only
    # ever the illustration names.
    returned = set(re.findall(r'"([a-z_]+)"', src))
    assert returned == set(_ElsBarMoveTypes.ALL), (
        f"_get_move_type returns {sorted(returned)} but the roster is "
        f"{sorted(_ElsBarMoveTypes.ALL)} -- register it, or these tests do not "
        f"cover it")
