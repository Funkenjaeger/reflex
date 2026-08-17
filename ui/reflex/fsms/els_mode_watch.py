"""Mode watch: compare the UI's model against the firmware's published mode.

Rung 2 of the 2026-08-16 architecture direction. The firmware (mode-watch
probe, diag schema 4) derives its machine mode as a pure function of its own
registers and publishes it continuously. This module compares that against the
domain FSM's state and produces two kinds of output, both LOG-ONLY:

- a CENSUS: every (fsm_state, mode) pair observed, counted, first sighting
  logged. This is the data the whole direction runs on — which pairings
  actually occur in real use decides how the mode taxonomy is refined and
  when the UI may start ACTING on the published mode. No one's confidence
  substitutes for it.

- a DIVERGENCE alarm for the small set of pairings we are already confident
  are wrong (the machine holding or taking up backlash with no session job;
  anything motion-armed while the FSM is in alarm). Debounced over
  consecutive samples so a transition caught mid-flight does not cry wolf,
  and reported once per episode so weeks of logs stay readable.

WHY LOG-ONLY, still: acting on a divergence means teardown, and the teardown
paths are exactly what is under repair. Escalation gets designed against this
module's output, not before it (same reasoning as the servoMode watchdog in
dispatchers/servo.py).

Pure on purpose — no Kivy, no board, no clock. The controller feeds it
(state, mode) samples; it returns what happened so the wiring stays a line.

Mode values mirror reflex-fw's els_machine_mode.h (ELS_MMODE_*), pinned there
by els_machine_mode_test as a wire contract: append, never renumber.
"""

import logging

log = logging.getLogger(__name__)

MODE_OFF = 0
MODE_IDLE = 1
MODE_FEEDING = 2
MODE_MOVING = 3
MODE_JOG = 4
MODE_HELD = 5
MODE_TAKEUP = 6
MODE_CAL = 7

MODE_NAMES = {
    MODE_OFF: "OFF",
    MODE_IDLE: "IDLE",
    MODE_FEEDING: "FEEDING",
    MODE_MOVING: "MOVING",
    MODE_JOG: "JOG",
    MODE_HELD: "HELD",
    MODE_TAKEUP: "TAKEUP",
    MODE_CAL: "CAL",
}

# The confident-bad set, deliberately small. Everything else is census data,
# not a defect — 'stopped' seeing OFF is the arm-refused path, 'cutting'
# seeing HELD is the stop firing before the FSM hears about it, and so on.
# Growing this set is a decision the census earns, one pair at a time.
DIVERGENT = frozenset({
    # No session job, yet the machine is holding position at a stop or
    # driving a backlash takeup: the exact "machine does something the UI
    # never asked for" class the review documented.
    ("disabled", MODE_HELD),
    ("disabled", MODE_TAKEUP),
    # A takeup only ever follows a released cut; 'stopped' never released one.
    ("stopped", MODE_TAKEUP),
    # Alarm teardown drives the machine to a safe idle; anything motion-armed
    # after it says the teardown did not land.
    ("alarm", MODE_FEEDING),
    ("alarm", MODE_MOVING),
    ("alarm", MODE_HELD),
    ("alarm", MODE_TAKEUP),
})


def mode_name(mode: int) -> str:
    return MODE_NAMES.get(mode, f"UNKNOWN({mode})")


class ElsModeWatch:
    """Feed (fsm_state, mode) samples; get 'census' / 'divergence' / None back."""

    # Consecutive divergent samples before reporting. The firmware republishes
    # at ~10 Hz and the controller samples at ~6 Hz, so 3 in a row is roughly
    # half a second — long enough to skip any legitimate transition skew
    # between the two state holders, far too short to miss a real hold.
    CONSECUTIVE = 3

    def __init__(self):
        self.pair_counts = {}
        self._bad_run = 0
        self._bad_pair = None
        self._episode_reported = False

    def feed(self, state: str, mode: int):
        """One sample. Returns 'census' on a first-ever pairing, 'divergence'
        when a confident-bad pairing has persisted long enough (once per
        episode), else None. Logs in all three cases so the wiring does not
        have to."""
        pair = (state, mode)
        first = pair not in self.pair_counts
        self.pair_counts[pair] = self.pair_counts.get(pair, 0) + 1
        if first:
            log.info(
                f"mode-watch census: first sighting of "
                f"(fsm={state}, mode={mode_name(mode)})"
            )

        if pair in DIVERGENT:
            if pair == self._bad_pair:
                self._bad_run += 1
            else:
                self._bad_pair = pair
                self._bad_run = 1
                self._episode_reported = False
            if self._bad_run >= self.CONSECUTIVE and not self._episode_reported:
                self._episode_reported = True
                log.warning(
                    f"mode-watch DIVERGENCE: FSM says '{state}' but the "
                    f"firmware reports {mode_name(mode)} for {self._bad_run} "
                    f"consecutive samples. The machine is doing something "
                    f"this session's model says it should not be. LOG-ONLY; "
                    f"see els_mode_watch.py for why no action is taken."
                )
                return 'divergence'
            return 'census' if first else None

        # Healthy sample: close any open episode.
        if self._bad_run and self._episode_reported:
            log.info(
                f"mode-watch divergence CLEARED after {self._bad_run} samples "
                f"(now fsm={state}, mode={mode_name(mode)})"
            )
        self._bad_run = 0
        self._bad_pair = None
        self._episode_reported = False
        return 'census' if first else None
