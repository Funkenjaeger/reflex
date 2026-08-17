"""Tests for ElsUiFsm conditional transitions used by non-wizard modes.

The UI FSM is a thin coordinator that reads validity and mode flags off
its controller. We stub the controller with SimpleNamespace so the FSM
can be exercised in isolation, per the testing strategy in the project's
kivy-fsm-design-pattern.md doc.
"""
from types import SimpleNamespace

import pytest

from rcp.fsms.ui_fsm import ElsUiFsm


def _make_controller(**overrides):
    defaults = dict(
        stop_z_valid=True,
        retract_z_valid=True,
        start_dia_valid=True,
        stop_dia_valid=True,
        wizard_enabled=False,
        retract_enabled=False,
        # on_enter_in_cycle_* hooks call into the controller; in tests we just
        # need them to exist as no-ops.
        start_cut=lambda: None,
        start_retract=lambda: None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def wizard_fsm():
    return ElsUiFsm(_make_controller(wizard_enabled=True, retract_enabled=True))


@pytest.fixture
def stop_only_fsm():
    return ElsUiFsm(_make_controller())


@pytest.fixture
def stop_retract_fsm():
    return ElsUiFsm(_make_controller(retract_enabled=True))


# ─── start: branches on wizard_enabled ──────────────────────────────────────

def test_start_in_wizard_mode_enters_set_stop_z(wizard_fsm):
    assert wizard_fsm.state == "idle"
    wizard_fsm.start()
    assert wizard_fsm.state == "set_stop_z"


def test_start_in_stop_only_jumps_to_in_cycle(stop_only_fsm):
    assert stop_only_fsm.state == "idle"
    stop_only_fsm.start()
    assert stop_only_fsm.state == "in_cycle.waiting_to_cut"


def test_start_in_stop_retract_jumps_to_in_cycle(stop_retract_fsm):
    assert stop_retract_fsm.state == "idle"
    stop_retract_fsm.start()
    assert stop_retract_fsm.state == "in_cycle.waiting_to_cut"


# ─── cut_done: branches on retract_enabled ─────────────────────────────────

def test_cut_done_stop_only_returns_to_waiting_to_cut(stop_only_fsm):
    stop_only_fsm.start()
    stop_only_fsm.action()
    assert stop_only_fsm.state == "in_cycle.cutting"
    stop_only_fsm.cut_done()
    assert stop_only_fsm.state == "in_cycle.waiting_to_cut"


def test_cut_done_stop_retract_enters_waiting_to_retract(stop_retract_fsm):
    stop_retract_fsm.start()
    stop_retract_fsm.action()
    assert stop_retract_fsm.state == "in_cycle.cutting"
    stop_retract_fsm.cut_done()
    assert stop_retract_fsm.state == "in_cycle.waiting_to_retract"


def test_cut_done_wizard_enters_waiting_to_retract(wizard_fsm):
    # Walk the wizard to in_cycle.waiting_to_cut, then take a cut.
    wizard_fsm.start()
    wizard_fsm.action()  # set_stop_z → set_retract_z
    wizard_fsm.action()  # set_retract_z → set_start_dia
    wizard_fsm.action()  # set_start_dia → set_stop_dia
    wizard_fsm.action()  # set_stop_dia → confirm
    wizard_fsm.action()  # confirm → in_cycle (waiting_to_cut)
    assert wizard_fsm.state == "in_cycle.waiting_to_cut"
    wizard_fsm.action()  # waiting_to_cut → cutting
    wizard_fsm.cut_done()
    assert wizard_fsm.state == "in_cycle.waiting_to_retract"


# ─── retract loop: stop+retract goes all the way around ─────────────────────

def test_stop_retract_cycle_loops_back_to_waiting_to_cut(stop_retract_fsm):
    stop_retract_fsm.start()
    stop_retract_fsm.action()      # cutting
    stop_retract_fsm.cut_done()    # waiting_to_retract
    stop_retract_fsm.action()      # retracting
    stop_retract_fsm.retract_done()
    assert stop_retract_fsm.state == "in_cycle.waiting_to_cut"


# ─── cancel: leaves any in-cycle state back to idle ─────────────────────────

def test_cancel_from_in_cycle_returns_to_idle(stop_retract_fsm):
    stop_retract_fsm.start()
    assert stop_retract_fsm.state == "in_cycle.waiting_to_cut"
    stop_retract_fsm.cancel()
    assert stop_retract_fsm.state == "idle"


def test_cancel_from_each_wizard_step_returns_to_idle(wizard_fsm):
    for step in ("set_stop_z", "set_retract_z", "set_start_dia",
                 "set_stop_dia", "confirm"):
        wizard_fsm.fsm.set_state(step)
        wizard_fsm.cancel()
        assert wizard_fsm.state == "idle"


# ─── wizard happy path: full walk through every step ────────────────────────

def test_wizard_full_walk(wizard_fsm):
    sequence = [
        ("idle",                       "set_stop_z"),
        ("set_stop_z",                 "set_retract_z"),
        ("set_retract_z",              "set_start_dia"),
        ("set_start_dia",              "set_stop_dia"),
        ("set_stop_dia",               "confirm"),
        ("confirm",                    "in_cycle.waiting_to_cut"),
        ("in_cycle.waiting_to_cut",    "in_cycle.cutting"),
    ]
    wizard_fsm.start()  # idle → set_stop_z
    assert wizard_fsm.state == "set_stop_z"
    # walk the rest via action()
    for _, expected in sequence[1:]:
        wizard_fsm.action()
        assert wizard_fsm.state == expected


# ─── validity gating on wizard `action` transitions ────────────────────────

def test_action_blocked_when_stop_z_invalid():
    fsm = ElsUiFsm(_make_controller(wizard_enabled=True, stop_z_valid=False))
    fsm.start()
    assert fsm.state == "set_stop_z"
    fsm.action()  # should not advance — condition fails
    assert fsm.state == "set_stop_z"


def test_action_blocked_when_retract_z_invalid():
    fsm = ElsUiFsm(_make_controller(wizard_enabled=True, retract_z_valid=False))
    fsm.start()
    fsm.action()  # set_stop_z → set_retract_z
    assert fsm.state == "set_retract_z"
    fsm.action()
    assert fsm.state == "set_retract_z"


def test_action_blocked_when_stop_dia_invalid():
    fsm = ElsUiFsm(_make_controller(wizard_enabled=True, stop_dia_valid=False))
    fsm.start()
    fsm.action(); fsm.action(); fsm.action()
    assert fsm.state == "set_stop_dia"
    fsm.action()
    assert fsm.state == "set_stop_dia"


# ─── back navigation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("from_state, expected", [
    ("set_retract_z", "set_stop_z"),
    ("set_start_dia", "set_retract_z"),
    ("set_stop_dia",  "set_start_dia"),
    ("confirm",       "set_stop_dia"),
])
def test_back_navigation(wizard_fsm, from_state, expected):
    wizard_fsm.fsm.set_state(from_state)
    wizard_fsm.back()
    assert wizard_fsm.state == expected


# ─── manual carriage motion: bus event handlers ─────────────────────────────

def test_manual_retract_done_advances_to_waiting_to_cut(stop_retract_fsm):
    stop_retract_fsm.fsm.set_state("in_cycle.waiting_to_retract")
    stop_retract_fsm.manual_retract_done()
    assert stop_retract_fsm.state == "in_cycle.waiting_to_cut"


def test_carriage_unretracted_falls_back_to_waiting_to_retract(stop_retract_fsm):
    stop_retract_fsm.fsm.set_state("in_cycle.waiting_to_cut")
    stop_retract_fsm.carriage_unretracted()
    assert stop_retract_fsm.state == "in_cycle.waiting_to_retract"


def test_els_stop_event_completes_a_cut_in_progress():
    fsm = ElsUiFsm(_make_controller(retract_enabled=True))
    fsm.start()
    fsm.action()  # waiting_to_cut → cutting
    fsm.on_event_els_stop_activated()
    assert fsm.state == "in_cycle.waiting_to_retract"


def test_els_stop_event_ignored_outside_cutting():
    fsm = ElsUiFsm(_make_controller(retract_enabled=True))
    fsm.start()
    # In waiting_to_cut, els_stop_activated must be a no-op (the cut_done
    # transition has source=in_cycle.cutting only).
    fsm.on_event_els_stop_activated()
    assert fsm.state == "in_cycle.waiting_to_cut"


def test_retract_done_event_completes_a_retract_in_progress():
    fsm = ElsUiFsm(_make_controller(retract_enabled=True))
    fsm.fsm.set_state("in_cycle.retracting")
    fsm.on_event_els_retract_done()
    assert fsm.state == "in_cycle.waiting_to_cut"


# ─── alarm / recovery ───────────────────────────────────────────────────────

def test_fault_from_any_state_enters_alarm(stop_retract_fsm):
    stop_retract_fsm.start()
    stop_retract_fsm.fault()
    assert stop_retract_fsm.state == "alarm"


def test_ack_alarm_returns_to_idle(stop_retract_fsm):
    stop_retract_fsm.fault()
    stop_retract_fsm.ack_alarm()
    assert stop_retract_fsm.state == "idle"


# ─── broadcast: on_enter callbacks invoke controller hooks ──────────────────

def test_entering_in_cycle_cutting_calls_controller_start_cut():
    calls = []
    controller = _make_controller(
        retract_enabled=True,
        start_cut=lambda: calls.append("start_cut"),
        start_retract=lambda: calls.append("start_retract"),
    )
    fsm = ElsUiFsm(controller)
    fsm.start()        # idle → in_cycle.waiting_to_cut
    fsm.action()       # → in_cycle.cutting (fires on_enter_in_cycle_cutting)
    assert calls == ["start_cut"]


def test_entering_in_cycle_retracting_calls_controller_start_retract():
    calls = []
    controller = _make_controller(
        retract_enabled=True,
        start_cut=lambda: calls.append("start_cut"),
        start_retract=lambda: calls.append("start_retract"),
    )
    fsm = ElsUiFsm(controller)
    fsm.start(); fsm.action(); fsm.cut_done(); fsm.action()
    # The sequence should land in retracting and have triggered both callbacks.
    assert calls == ["start_cut", "start_retract"]
    assert fsm.state == "in_cycle.retracting"
