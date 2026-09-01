"""X reads diameter: the cross-slide readout shows the work's diameter rather
than how far the slide physically travelled.

WHY IT EXISTS. Without it the only way to get a diameter DRO is to enter a
scale at twice the real resolution, and nothing then records that the doubling
was intended. On elspi that hid a genuine misprovisioning for months: a 1 um
head entered at 2.5 um, the X DRO reading 2.5x true travel, invisible because
"the scale is just what it says".

THE RULE THESE TESTS EXIST TO PIN: the doubling is the OUTERMOST operation, and
symmetric on every path. Offsets stay in radius units, so zeroing and tool
offsets are untouched and the encoder round trip stays exact. Break the
symmetry on one side and a typed diameter commits to the wrong encoder position
-- which on a threading retract is a factor of two in the gate that keeps the
tool out of the groove.

WHAT IS DELIBERATELY NOT TESTED HERE: that the ELS gates still compare
correctly. They compare display-domain against display-domain (x > start_dia),
and doubling both sides is monotone -- 2a > 2b iff a > b -- so there is nothing
this feature can break there and an assertion would be theatre.
"""
from unittest.mock import MagicMock

import pytest

from tests.dispatchers.conftest import (MockBoard, MockFormats,
                                        MockOffsetProvider)
from reflex.dispatchers.axis import AxisDispatcher
from reflex.dispatchers.axis_transform import AxisTransform
from reflex.dispatchers.input import InputDispatcher
from reflex.dispatchers.servo import ServoDispatcher


# Fixtures mirror test_axis_dispatcher.py's, which are module-local there
# rather than in a conftest. Duplicated instead of relocated: ~40 existing
# tests depend on those, and moving them is a far larger blast radius than
# these dozen lines.

@pytest.fixture
def board():
    b = MockBoard()
    b.device = MagicMock()
    return b


@pytest.fixture
def formats():
    return MockFormats()


@pytest.fixture
def offset_provider():
    return MockOffsetProvider()


@pytest.fixture
def servo(board, formats, tmp_path, monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    return ServoDispatcher(board=board, formats=formats, id_override="dia_servo")


@pytest.fixture
def inputs(board, tmp_path, monkeypatch):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    return [InputDispatcher(board=board, inputIndex=i,
                            id_override=f"dia_input_{i}") for i in range(4)]


def _make_axis(board, formats, servo, offset_provider, inputs, tmp_path,
               monkeypatch, transform, id_override):
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / ".config" / "reflex"))
    return AxisDispatcher(
        board=board, formats=formats, servo=servo,
        offset_provider=offset_provider, inputs=inputs,
        transform=transform, id_override=id_override,
    )


@pytest.fixture
def axis(board, formats, servo, offset_provider, inputs, tmp_path, monkeypatch):
    return _make_axis(board, formats, servo, offset_provider, inputs, tmp_path,
                      monkeypatch, AxisTransform.identity(0), "dia_axis_0")


@pytest.fixture
def dia_axis(axis, inputs):
    """A 1 um scale reading diameter, which is elspi's real X once the scale is
    entered faithfully."""
    inputs[0].ratioNum = 1
    inputs[0].ratioDen = 1000
    inputs[0].position = 0
    axis.diameter_mode = True
    axis._update_position()
    return axis


# ── the factor itself ───────────────────────────────────────────────────────

def test_off_by_default(axis):
    """Turning it on must never be something that happened to a user."""
    assert axis.diameter_mode is False
    assert axis.dia_factor == 1.0


def test_on_doubles(axis):
    axis.diameter_mode = True
    assert axis.dia_factor == 2.0


def test_spindle_is_exempt_even_when_set(axis):
    """A spindle reads degrees; a diameter is meaningless there. Guarded in one
    place so the exemption cannot be got wrong per call site."""
    axis.diameter_mode = True
    axis.spindleMode = True
    assert axis.dia_factor == 1.0


# ── display ─────────────────────────────────────────────────────────────────

def test_the_readout_moves_twice_as_fast_as_the_slide(dia_axis, inputs):
    """1000 counts at 1 um is 1 mm of travel -- and 2 mm of diameter."""
    inputs[0].position = 1000
    dia_axis._update_position()
    assert dia_axis.scaledPosition == pytest.approx(2.0)


def test_without_it_the_readout_is_the_travel(axis, inputs):
    """The negative control. Same fixture, flag off."""
    inputs[0].ratioNum = 1
    inputs[0].ratioDen = 1000
    inputs[0].position = 1000
    axis.diameter_mode = False
    axis._update_position()
    assert axis.scaledPosition == pytest.approx(1.0)


# ── typing a value means DIAMETER (Evan, 2026-09-01) ────────────────────────

def test_typing_a_value_sets_the_diameter(dia_axis, inputs):
    """"Enter 20.000 and the readout reads 20.000" -- not "call this radius 20"."""
    inputs[0].position = 5000
    dia_axis._update_position()
    dia_axis.set_current_position(20.0)
    dia_axis._update_position()
    assert dia_axis.scaledPosition == pytest.approx(20.0)


def test_after_setting_a_diameter_the_slide_still_moves_it_by_two(dia_axis, inputs):
    """The consequence of the above, and the reason the halving goes INSIDE the
    offset arithmetic: 1 mm of travel from a set point is 2 mm of diameter."""
    inputs[0].position = 5000
    dia_axis._update_position()
    dia_axis.set_current_position(20.0)
    inputs[0].position = 6000                      # +1000 counts = +1 mm travel
    dia_axis._update_position()
    assert dia_axis.scaledPosition == pytest.approx(22.0)


def test_zeroing_is_unaffected(dia_axis, inputs):
    """0/2 is 0 either way -- worth pinning because zero is the operation an
    operator performs constantly and would notice last."""
    inputs[0].position = 7777
    dia_axis._update_position()
    dia_axis.zero_position()
    dia_axis._update_position()
    assert dia_axis.scaledPosition == pytest.approx(0.0, abs=0.01)


# ── the encoder round trip, which is where a factor of two would hide ───────

def test_roundtrip_holds_with_no_offset(dia_axis, inputs):
    inputs[0].position = 5000
    dia_axis._update_position()
    assert dia_axis.position_to_encoder(dia_axis.scaledPosition) == 5000


def test_roundtrip_holds_after_zeroing(dia_axis, inputs):
    """Offsets are radius-domain. If the doubling leaked inside them, this is
    the test that goes red."""
    inputs[0].position = 5000
    dia_axis.zero_position()
    inputs[0].position = 9200
    dia_axis._update_position()
    assert dia_axis.position_to_encoder(dia_axis.scaledPosition) == 9200


def test_roundtrip_holds_after_setting_a_diameter(dia_axis, inputs):
    inputs[0].position = 5000
    dia_axis.set_current_position(20.0)
    inputs[0].position = 6000
    dia_axis._update_position()
    assert dia_axis.position_to_encoder(dia_axis.scaledPosition) == 6000


def test_the_two_conversions_are_inverses(dia_axis, inputs):
    """scaled_from_encoder and position_to_encoder are the pair _commit_start_dia
    round-trips through. Asymmetry here is the failure that puts a committed
    diameter at the wrong physical place."""
    inputs[0].position = 3000
    dia_axis.zero_position()
    for enc in (0, 1234, -987, 50000):
        assert dia_axis.position_to_encoder(
            dia_axis.scaled_from_encoder(enc)) == enc


def test_scaled_from_encoder_agrees_with_the_live_readout(dia_axis, inputs):
    """The two must describe the same axis: a committed target re-rendered from
    its frozen encoder has to land where the DRO says that encoder is."""
    inputs[0].position = 4321
    dia_axis._update_position()
    assert dia_axis.scaled_from_encoder(4321) == pytest.approx(
        dia_axis.scaledPosition)


# ── it does not leak into the motion path ───────────────────────────────────

def test_it_does_not_touch_the_sync_ratio(axis, inputs, board):
    """_set_sync_ratio reads ratioNum/ratioDen straight off the INPUT, which is
    exactly why the doubling was moved out of there. If flipping this changed
    the sync ratio, it would be changing cutting motion."""
    inputs[0].ratioNum = 1
    inputs[0].ratioDen = 1000
    board.connected = True
    axis.diameter_mode = False
    axis._set_sync_ratio()
    before = (axis.syncRatioNum, axis.syncRatioDen,
              inputs[0].ratioNum, inputs[0].ratioDen)

    axis.diameter_mode = True
    axis._set_sync_ratio()
    after = (axis.syncRatioNum, axis.syncRatioDen,
             inputs[0].ratioNum, inputs[0].ratioDen)

    assert before == after


def test_it_does_not_touch_the_stored_scale(dia_axis, inputs):
    """The whole point: the scale stays the truth about the head. Turning this
    on must not rewrite it -- that is the hack being replaced."""
    assert (inputs[0].ratioNum, inputs[0].ratioDen) == (1, 1000)


# ── a summed axis: why the flag is on the axis, not the input ───────────────

def test_a_summed_axis_carries_one_flag(board, formats, servo, offset_provider,
                                        inputs, tmp_path, monkeypatch):
    """The decisive argument for axis-level storage. A per-input flag would give
    a summed cross slide two flags and no defined answer."""
    ax = _make_axis(board, formats, servo, offset_provider, inputs, tmp_path,
                    monkeypatch, AxisTransform.sum(0, 1), "test_axis_sum")
    inputs[0].ratioNum = inputs[1].ratioNum = 1
    inputs[0].ratioDen = inputs[1].ratioDen = 1000
    inputs[0].position = 1000
    inputs[1].position = 1000
    ax.diameter_mode = True
    ax._update_position()

    # 2 mm of summed travel, doubled once -- not once per contributing input.
    assert ax.scaledPosition == pytest.approx(4.0)
