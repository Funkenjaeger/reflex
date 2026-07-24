"""Task 5 verification: the real object-graph harness assembles, connects to the
emulator, reads a known register, drives the production toggle write-path, and
sees it affect the live physics -- with no Kivy App/UI."""

from fractions import Fraction

import pytest

pytestmark = pytest.mark.system


def test_harness_assembles_and_connects(harness):
    assert harness.connected
    # The whole real FSM stack was constructed.
    assert harness.els is not None
    assert harness.controller is not None
    assert harness.els_fsm is not None
    assert harness.ui_fsm is not None
    assert harness.hal is not None
    # Domain FSM starts disabled.
    assert harness.els_fsm.state == "disabled"


def test_harness_reads_known_register(harness):
    # servoDir is readable over the live Modbus link.
    assert harness.register("servo", "servoDir") in (1, -1)
    # scales array reads back too.
    assert isinstance(harness.carriage_position_counts(), int)


def test_commissioned_thread_geometry_matches_emulator_physics(harness):
    """Host-side twin of the emulator dashboard's geometry cross-check
    (reflex-fw dashboard.cpp): the count-domain thread geometry the UI pushes
    must satisfy zCountsPerPitch / threadPitchSteps == the physics'
    z-counts-per-leadscrew-step (lathe.toml: mm_per_step 0.00396875 ×
    encoder_counts_per_mm 400 = 1.5875). With the hermetic defaults the UI
    pushes ~1.1111 instead and the dashboard (interactive mode) logs
    'WARN geom mismatch' — commission_geometry() exists to close exactly that
    gap, so pin it here where the serve-mode tests can see it."""
    h = harness
    h.configure(is_threading=True, retract_enabled=False, wizard_enabled=False,
                els_forward=True)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))   # 16 TPI (feeds.py Thread IN "16")

    h.els_fsm.push_thread_geometry()
    h.pump()

    tps = float(h.register("elsStop", "threadPitchSteps"))
    zcpp = float(h.register("elsStop", "zCountsPerPitch"))
    assert tps != 0.0 and zcpp != 0.0, "thread geometry was not pushed"
    # Same 0.5% relative tolerance the emulator dashboard uses (float32
    # registers round the exact host Fractions slightly).
    assert zcpp / tps == pytest.approx(1.5875, rel=0.005), (
        f"geom mismatch: firmware zCnt/lsStep={zcpp / tps} "
        f"(zcpp={zcpp} tps={tps}), physics=1.5875"
    )


def test_servo_reverse_toggle_writes_through_production_path(harness):
    """Setting ServoDispatcher.reverse fires its real handler and flips the
    servoDir register the firmware reads -- the exact write-path production
    uses (Open Item 2 acceptance criterion), not a direct register poke."""
    # reverse defaults False (servoDir=+1 on connect). Toggle True THEN back to
    # False so both writes actually fire the _on_reverse_changed handler -- a
    # set_servo_reverse(False) first would be a no-op (no property change) and
    # only re-read the on-connect write.
    harness.set_servo_reverse(True)
    harness.pump()
    assert harness.register("servo", "servoDir") == -1

    harness.set_servo_reverse(False)
    harness.pump()
    assert harness.register("servo", "servoDir") == 1
