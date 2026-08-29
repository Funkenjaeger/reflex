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


# ─── DRO row height: a deliberate exception, recorded ──────────────────────
# The rows are the one thing on this screen that is not allowed to change size,
# and until 2026-08-29 nothing tested their arithmetic at all -- which is how
# `dro_els_gap`'s own comment came to claim a symmetry it never produced (it
# read "symmetric with the gap under the top status bar", while the rendered
# gaps were 8 px at the top and 13 at the bottom for over a week).

def _rows_stub(height=562, els_bar_h=128, adv_h=128, cap=150, n_axes=2):
    """Stand-in exposing only what _update_row_heights touches.

    Defaults are the real 1024x600 geometry: a 562 px layout above a 128 px ELS
    bar and a 128 px advanced bar, so `available` is 306 -- the numbers the
    change below was actually chosen against.
    """
    from types import SimpleNamespace as NS
    return NS(
        height=height,
        els_bar=NS(height=els_bar_h),
        els_adv_bar=NS(height=adv_h),
        app=NS(formats=NS(max_row_height=cap)),
        spindle_info=NS(size_hint_y=1, height=0),
        axis_bars=[NS(size_hint_y=1, height=0) for _ in range(n_axes)],
    )


def test_dro_rows_are_101px_a_deliberate_2026_08_29_exception():
    """The rows grew 99 -> 101 px ON PURPOSE. This records it.

    There is a standing rule that the DRO rows do not resize, and this change
    broke it knowingly, on Evan's explicit call, after the trade was rendered
    and measured. What it bought: the gap below the DRO stack went 13 px -> 8,
    matching the 8 px above it. What it cost: the digit glyphs went 87 -> 90 px,
    because dro_coordbar.kv derives `max_font_size` from the row height.

    THE SPACER CANNOT BE SHRUNK ON ITS OWN, which is the part worth pinning.
    It has size_hint_y 1, so it is the REMAINDER: with `available` fixed, five
    pixels taken out of it necessarily land in the rows. Anyone reading
    `dro_els_gap` as "the visible gap" will make that mistake again -- and the
    two are not even equal, because the spindle row contributes ~5 px of its
    own glyph whitespace below the digits before this gap is added.

    The rule this does NOT relax: nothing may resize in response to TRANSIENT
    state. Notice strips still overlay rather than grow the bar, and the tests
    above still hold the bar's height to `natural_height`.
    """
    obj = _rows_stub()
    ElsModeLayout._update_row_heights(obj)

    assert obj.spindle_info.height == 101, (
        "DRO row height changed. It is 101 px by a deliberate 2026-08-29 "
        "decision (dro_els_gap dp(8) -> dp(3)); if you are changing it again, "
        "that is Evan's call to make, not a refactor -- the digit size moves "
        f"with it. Got {obj.spindle_info.height}."
    )
    for bar in obj.axis_bars:
        assert bar.height == obj.spindle_info.height, (
            "every DRO row must be the same height as the spindle row")

    # The spacer is what is left, not what was asked for.
    available = obj.height - obj.els_bar.height - obj.els_adv_bar.height
    spacer = available - obj.spindle_info.height * 3
    assert spacer == 3, (
        f"the leftover the spacer absorbs should be the dp(3) gap; got {spacer}")


def test_the_row_height_cap_still_wins_on_a_taller_screen():
    """`min(..., max_row_height)` is not decoration.

    On a screen tall enough that an even share would exceed the cap, the rows
    must stop growing and the spacer must take the rest -- otherwise the DRO
    digits scale without bound. Pinned because the arithmetic above is one
    edit away from dropping the min().
    """
    obj = _rows_stub(height=1200, cap=150)
    ElsModeLayout._update_row_heights(obj)
    assert obj.spindle_info.height == 150
