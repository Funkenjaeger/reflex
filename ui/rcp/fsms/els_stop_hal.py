"""Hardware abstraction layer for the elsStop register block.

The HAL is the single place that knows about firmware register names and
encoding. Methods are named in domain terms; callers (the FSM) never see
register keys. Replacing the firmware protocol means writing one new HAL.
"""
from kivy.logger import Logger

log = Logger.getChild(__name__)


class ElsStopHal:
    """Domain-named operations against the elsStop register block."""

    # ELS Stop direction mirrors the firmware convention: -1 when the
    # operator's "forward" direction matches positive scale travel.
    DIR_FORWARD = -1
    DIR_REVERSE = 1

    # Hysteresis values used by the firmware to debounce the stop trigger.
    HYSTERESIS_TIGHT = 0      # active retract / wizard — stop on first crossing
    HYSTERESIS_LOOSE = 800    # standalone stop — tolerate small overshoot

    def __init__(self, board):
        self._board = board

    @property
    def connected(self) -> bool:
        return self._board.connected

    # ── enable / active ───────────────────────────────────────────────
    def set_enable(self, enabled: bool) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['enable'] = 1 if enabled else 0

    def set_active(self, active: bool) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['active'] = 1 if active else 0

    def read_active(self) -> bool:
        if not self._board.connected:
            return False
        return bool(self._board.device['elsStop']['active'])

    # ── direction / hysteresis ────────────────────────────────────────
    def set_stop_direction(self, forward: bool) -> None:
        if not self._board.connected:
            return
        d = self.DIR_FORWARD if forward else self.DIR_REVERSE
        self._board.device['elsStop']['stopDirection'] = d

    def set_hysteresis_tight(self) -> None:
        self._set_hysteresis(self.HYSTERESIS_TIGHT)

    def set_hysteresis_loose(self) -> None:
        self._set_hysteresis(self.HYSTERESIS_LOOSE)

    def _set_hysteresis(self, counts: int) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['hysteresis'] = counts

    # ── stop target / scale source ────────────────────────────────────
    def set_stop_position(self, encoder_counts: int) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['stopPosition'] = encoder_counts

    def set_scale_index(self, scale_index: int) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['scaleIndex'] = scale_index

    def set_steps_to_go(self, steps: int) -> None:
        if not self._board.connected:
            return
        self._board.device['servo']['stepsToGo'] = steps

    # ──  ── 
    def set_thread_pitch_steps(self, tps_value: float) -> None:
        if not self._board.connected:
            return
        self._board.device['elsStop']['threadPitchSteps'] = tps_value

    def is_move_done(self) -> bool:
        return self._board.device['servo']['stepsToGo'] == 0
