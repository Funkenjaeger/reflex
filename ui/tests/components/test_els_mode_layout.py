"""ElsModeLayout's ownership of the advanced bar's height.

This file exists because of a bug that survived a "fix" and a machine test.

The take-up warning strip rendered over a DRO row instead of inside the ELS bar.
The obvious cause was that ElsAdvancedBar's kv height expression did not account
for that strip, so the children exceeded the parent. That was true, and fixing it
changed nothing on the machine -- because ElsModeLayout ASSIGNS the bar's height,
and in Kivy assigning to a property replaces any kv binding on it. The kv
expression was correct and simply not driving anything. Worse, the height being
assigned was captured once at construction, before any strip existed.

So the structural kv test passed, the fix shipped, and the bar was still wrong.
A test that checks an expression is right cannot tell you the expression is
being used. These tests assert the behaviour instead.

`_apply_adv_visibility` is exercised as an UNBOUND method against a stub, because
building the real widget tree is not possible under test -- the mock GL backend
segfaults on real textures (see test_els_advbar.py, which patches the kv rule
tree out for the same reason).
"""

from types import SimpleNamespace

from reflex.components.home.els_mode_layout import ElsModeLayout


def _stub(natural_height=128, enable_advanced=True):
    """Minimal stand-in exposing only what _apply_adv_visibility touches."""
    return SimpleNamespace(
        els_bar=SimpleNamespace(enable_advanced=enable_advanced),
        els_adv_bar=SimpleNamespace(
            natural_height=natural_height, height=None, opacity=None, disabled=None),
        _update_row_heights=lambda *a: None,
    )


def test_height_follows_the_bar_s_current_natural_height():
    obj = _stub(natural_height=158)
    ElsModeLayout._apply_adv_visibility(obj)
    assert obj.els_adv_bar.height == 158


def test_height_tracks_a_strip_appearing_rather_than_a_stale_snapshot():
    """THE regression. A notice strip appearing raises natural_height; the bar
    must take the new value. Reading a construction-time snapshot instead is what
    pinned it at the base height and pushed the warning outside the bar."""
    obj = _stub(natural_height=128)
    ElsModeLayout._apply_adv_visibility(obj)
    assert obj.els_adv_bar.height == 128

    obj.els_adv_bar.natural_height = 158        # take-up warning appears
    ElsModeLayout._apply_adv_visibility(obj)
    assert obj.els_adv_bar.height == 158, (
        "bar did not grow when its natural height did -- a strip will render "
        "outside it, over whatever is above"
    )

    obj.els_adv_bar.natural_height = 128        # warning clears
    ElsModeLayout._apply_adv_visibility(obj)
    assert obj.els_adv_bar.height == 128


def test_hidden_bar_collapses_regardless_of_what_it_wants():
    """Visibility still outranks natural height: an advanced bar switched off
    must occupy nothing even while a strip is asking for room."""
    obj = _stub(natural_height=158, enable_advanced=False)
    ElsModeLayout._apply_adv_visibility(obj)
    assert obj.els_adv_bar.height == 0
    assert obj.els_adv_bar.opacity == 0
    assert obj.els_adv_bar.disabled is True


def test_shown_bar_is_visible_and_enabled():
    obj = _stub(natural_height=128)
    ElsModeLayout._apply_adv_visibility(obj)
    assert obj.els_adv_bar.opacity == 1
    assert obj.els_adv_bar.disabled is False
