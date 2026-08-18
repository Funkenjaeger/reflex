"""JogBar direction: the buttons mean PHYSICAL carriage direction.

sign(carriage) = sign(jogSpeed) x servoDir (pinned at the register level by
tests/system/test_jog_mode.py), so the bar multiplies its command by the
commissioned direction. elspi (reverse=True) jogged backwards against its
buttons for its entire life until 2026-08-17 — never noticed, because jog
was never used until the round-2 All-Features detour.

Logic-level tests (unbound update_jog on a stand-in): the kv wiring and
firmware semantics are covered by the system test.
"""
from types import SimpleNamespace

from reflex.components.home.jogbar import JogBar


def make(reverse, desired=1000):
    return SimpleNamespace(
        desired_speed=desired, enable_jog=False, enable_jog_reverse=False,
        app=SimpleNamespace(servo=SimpleNamespace(
            maxSpeed=10000, jogSpeed=None, servoMode=0, reverse=reverse)))


def test_forward_button_is_physical_forward_on_a_reversed_machine():
    f = make(reverse=True)
    f.enable_jog = True
    JogBar.update_jog(f, None, None)
    assert f.app.servo.jogSpeed == -1000    # x servoDir(-1) = physical +
    assert f.app.servo.servoMode == 2


def test_forward_button_unchanged_on_an_unreversed_machine():
    f = make(reverse=False)
    f.enable_jog = True
    JogBar.update_jog(f, None, None)
    assert f.app.servo.jogSpeed == 1000
    assert f.app.servo.servoMode == 2


def test_reverse_button_mirrors_in_both_commissionings():
    f = make(reverse=True)
    f.enable_jog_reverse = True
    JogBar.update_jog(f, None, None)
    assert f.app.servo.jogSpeed == 1000     # -desired x -1

    f = make(reverse=False)
    f.enable_jog_reverse = True
    JogBar.update_jog(f, None, None)
    assert f.app.servo.jogSpeed == -1000


def test_release_zeroes_speed_and_leaves_mode_alone():
    f = make(reverse=True)
    JogBar.update_jog(f, None, None)
    assert f.app.servo.jogSpeed == 0
    assert f.app.servo.servoMode == 0       # untouched by the idle branch
