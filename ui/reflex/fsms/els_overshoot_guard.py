"""What decides, each tick, whether the stop-overshoot correction may apply.

ElsUiController owns one. It is duck-typed and Kivy-free so every rule here is
tested directly (tests/fsms/test_els_overshoot_guard.py):

- THE TABLE IN EFFECT (table_in_counts): the machine's stored table, only
  while the setting is on, the table is not stale, and no measurement is
  running. Otherwise None, and the corrector writes 0.
- THE ONE-TIME MIGRATION (decided 2026-10-11). A machine that had the
  correction ON before the table could be stored, and has none stored, is
  given the built-in table as its own -- but only at the geometry it was
  measured under (els_overshoot_table.migration), and only for the setting as
  it was READ AT STARTUP. Switching the correction on later, with no table,
  is refused: that is a new machine asking for numbers it does not have.
  Turning it off instead would silently bring back a 0.2 mm overshoot into
  the next shoulder on the machine it was measured on.
- STALE (decided 2026-10-11): the binding differs from the live settings. The
  correction is set to off with one notice naming the change; the table is
  kept. This is the same check after a restart as at any other moment.
- THE OUTCOME WATCH and THE READOUT: a corrected stop landing past the
  target by more than PAST_TOLERANCE_COUNTS sets the correction to off, with a
  warning. A stop that lands short, the normal case, says nothing on screen
  (first run on the development lathe, 2026-10-11: a readout on every stop is distracting in
  normal use); its landing, in counts and display units, is kept as
  `last_landing` for the Measure window and logged.
- THE MEASUREMENT (els_overshoot_survey.py): while one runs the correction
  is held off and each stop goes to it instead. Its lines go to `cue()`, the
  ELS bar strip that stays until the next pass starts, not to a notice that
  times out (same visit: a cue on a timer was waited for, or lost). When a
  sweep ends, `on_result(survey)` opens its result for Save or Discard.

- THE GEARBOX MEASUREMENT (els_gear_survey.py) rides on the same guard: it is
  the same kind of guided air passes, and shares the cue strip and the
  Save-or-Discard window. While one runs, `gear_survey_running` is the hold
  that keeps the coupling-ratio check from acting (RatioWatch's `hold`), and
  each pass's final verdict and whole-pass reading arrive at `on_gear_pass`.
  Save writes the one position's factor into the gearbox table text through
  GearTable, by way of `apply_gear_table(text)`, which is the controller's own
  validated setter.

NEVER writes a register. It changes only ElsDispatcher settings and posts
notices.
"""
from __future__ import annotations

import datetime
from typing import Callable, Optional

from kivy.logger import Logger

from reflex.fsms import els_gear_survey as gear_mod
from reflex.fsms import els_overshoot_survey as survey_mod
from reflex.fsms import els_overshoot_table as tbl
from reflex.fsms.els_gear import NO_GEARBOX, GearTable, GearTableError
from reflex.fsms.els_ratio import Verdict
from reflex.fsms.els_stop_outcome import (
    PAST_TOLERANCE_COUNTS,
    StopOutcome,
    landed_text,
    past_off_text,
)
from reflex.utils.notices import MAX_SECONDS, NOTICE_INFO, NOTICE_WARNING

log = Logger.getChild(__name__)

# ── What the operator reads (65 characters at most, state then action) ───
MIGRATED_NOTICE = "Overshoot table is now saved as a machine setting."
NO_TABLE_NOTICE = "No overshoot table. Measure it in Advanced ELS settings."
NOT_THIS_MACHINE_NOTICE = "No overshoot table for this machine. Correction is off."
STALE_NOTICE = "{reason}. Overshoot correction is off."

#: How long the readout stays (the notice channel's longest).
READOUT_SECONDS = MAX_SECONDS


class OvershootGuard:
    """`els` is the ElsDispatcher (its els_overshoot_* settings); `binding()`
    the live Binding or None; `notify(message, severity, seconds=None)` posts a
    notice; `gear()` the engaged gearbox position name, for a measured
    table's source line; `today()` the date for it.

    The gearbox measurement's parts: `gear_table()` the configured table,
    `apply_gear_table(text)` the controller's setter for it (True when
    applied), `on_gear_result(survey)` a finished measurement for Save or
    Discard, and `on_lever_change(position)` called when the lever is moved
    to a position to be measured: the controller drives the passes in that
    position (ElsFsm.drive_for_measurement), which also clears a thread
    reference measured in another."""

    def __init__(self, els, binding: Callable[[], Optional[tbl.Binding]],
                 notify: Callable[..., object], gear: Callable[[], str] = lambda: "",
                 today: Callable[[], datetime.date] = datetime.date.today,
                 cue: Callable[[str], object] = lambda _text: None,
                 on_result: Callable[[survey_mod.Survey], object] = lambda _s: None,
                 gear_table: Callable[[], GearTable] = lambda: NO_GEARBOX,
                 apply_gear_table: Callable[[str], bool] = lambda _text: False,
                 on_gear_result: Callable[[gear_mod.GearSurvey], object] = lambda _s: None,
                 on_lever_change: Callable[[str], object] = lambda _p: None):
        self._els = els
        self._binding = binding
        self._notify = notify
        self._gear = gear
        self._today = today
        self._cue = cue
        self._on_result = on_result
        #: The last corrected stop's landing, as the Measure window shows it.
        self.last_landing: str = ""
        # The migration is for the setting as it was READ AT STARTUP.
        self._migrate = (getattr(els, "els_overshoot_correction", False) is True
                         and not getattr(els, "els_overshoot_table", ""))
        self.survey: Optional[survey_mod.Survey] = None
        self._survey_binding: Optional[tbl.Binding] = None
        self._gear_table = gear_table
        self._apply_gear_table = apply_gear_table
        self._on_gear_result = on_gear_result
        self._on_lever_change = on_lever_change
        self.gear_survey: Optional[gear_mod.GearSurvey] = None

    # ── reading the settings ────────────────────────────────────────────

    def stored(self) -> Optional[tbl.StoredTable]:
        e = self._els
        return tbl.load(getattr(e, "els_overshoot_table", ""),
                        getattr(e, "els_overshoot_mm_per_count", ""),
                        getattr(e, "els_overshoot_mm_per_step", ""),
                        getattr(e, "els_overshoot_gearbox", ""),
                        getattr(e, "els_overshoot_source", ""))

    def stale_reason(self) -> Optional[str]:
        """Why the stored table does not describe the machine now, or None
        (also None with no table, or the live settings unknown)."""
        stored, live = self.stored(), self._binding()
        if stored is None or live is None:
            return None
        return tbl.stale_reason(stored.binding, live)

    def status_text(self) -> str:
        return tbl.status_text(self.stored(), self.stale_reason())

    def _on(self) -> bool:
        return getattr(self._els, "els_overshoot_correction", False) is True

    def _set_off(self) -> None:
        self._els.els_overshoot_correction = False

    def _store(self, table: tbl.StoredTable) -> None:
        for k, v in tbl.persisted(table).items():
            setattr(self._els, k, v)

    # ── each tick ───────────────────────────────────────────────────────

    def check(self) -> None:
        """Migrate, refuse, or mark stale. Call once per tick, before the
        corrector reads table_in_counts()."""
        if not self._on():
            self._migrate = False
            return
        live = self._binding()
        if live is None:
            # Scale or leadscrew not provisioned: nothing can be judged, and
            # table_in_counts() is None until it can.
            return
        stored = self.stored()
        if stored is None:
            migrating = self._migrate
            self._migrate = False
            mig = tbl.migration(live) if migrating else None
            if mig is not None:
                self._store(mig)
                log.info("overshoot table: migrated the built-in table as this "
                         f"machine's ({tbl.format_points(mig.points)})")
                self._notify(MIGRATED_NOTICE, NOTICE_INFO)
                return
            self._set_off()
            text = NOT_THIS_MACHINE_NOTICE if migrating else NO_TABLE_NOTICE
            log.warning(f"overshoot correction refused: {text}")
            self._notify(text, NOTICE_WARNING)
            return
        self._migrate = False
        reason = tbl.stale_reason(stored.binding, live)
        if reason:
            self._set_off()
            log.warning(f"overshoot table stale: {reason}; correction set to off")
            self._notify(STALE_NOTICE.format(reason=reason), NOTICE_WARNING)

    def table_in_counts(self):
        """The table the corrector applies, in counts, or None."""
        if self.survey is not None and not self.survey.done:
            return None
        if not self._on():
            return None
        stored, live = self.stored(), self._binding()
        if stored is None or live is None:
            return None
        if tbl.stale_reason(stored.binding, live):
            return None
        return stored.in_counts()

    # ── each stop ───────────────────────────────────────────────────────

    def on_outcome(self, o: StopOutcome, factor: float) -> None:
        """A stop settled. `factor` is display units per mm."""
        live = self._binding()
        k = float(live.mm_per_count) if live is not None else 0.0
        log.info(f"ELS stop #{o.seq}: offset {o.offset}, rate "
                 f"{o.rate if o.rate is None else round(o.rate)} counts/s, "
                 f"past {o.past:+d}, coast {o.overshoot}, steps to go {o.steps_to_go}")
        s = self.survey
        if s is not None and not s.done:
            self._cue(s.add(o))
            if s.done and s.kind == survey_mod.SWEEP:
                self._on_result(s)
            return
        if not o.corrected or not live:
            return
        if not o.premise_ok:
            log.warning(f"ELS stop #{o.seq} not judged: {o.steps_to_go} steps "
                        "were still queued at the trigger")
            return
        self.last_landing = landed_text(o, k, factor)
        if o.past > PAST_TOLERANCE_COUNTS:
            if self._on():
                self._set_off()
            text = past_off_text(o, k, factor)
            log.warning(f"overshoot outcome watch: {text}")
            self._notify(text, NOTICE_WARNING, seconds=READOUT_SECONDS)
            return
        # Short, on target or within tolerance: nothing on screen.
        log.info(f"ELS stop #{o.seq}: {self.last_landing}")

    # ── the measurement ─────────────────────────────────────────────────

    def start_survey(self, kind: str) -> Optional[str]:
        """Start a detection pass or a sweep. Returns the reason it cannot
        start, or None when it has."""
        live = self._binding()
        if live is None:
            return "Set the Z scale and the leadscrew first."
        self.survey = survey_mod.Survey(kind, float(live.mm_per_count))
        self._survey_binding = live
        log.info(f"overshoot {kind} started")
        self._cue(self.survey.message)
        return None

    def cancel_survey(self) -> None:
        if self.survey is not None and not self.survey.done:
            log.info(f"overshoot {self.survey.kind} cancelled after "
                     f"{len(self.survey.stops)} stops")
        self.survey = None
        self._survey_binding = None
        self._cue("")

    def save_survey(self) -> Optional[str]:
        """Store a fitted sweep as this machine's table. Returns the reason it
        was not saved, or None. Saving does not turn the correction on."""
        s = self.survey
        if s is None or s.state != survey_mod.FITTED or not s.table:
            return "No sweep result to save."
        live = self._binding()
        if live is None or live != self._survey_binding:
            return "Settings changed during the sweep. Run it again."
        gear = self._gear()
        source = f"{tbl.SOURCE_MEASURED} {self._today().isoformat()}"
        if gear:
            source += f" in {gear}"
        table = tbl.from_counts(s.table, live, source)
        self._store(table)
        log.info(f"overshoot table saved ({source}): {tbl.format_points(table.points)}")
        self.survey = None
        self._survey_binding = None
        self._cue("")
        return None

    # ── the gearbox measurement ─────────────────────────────────────────

    def gear_table(self) -> GearTable:
        """The configured gearbox table, for the measurement's window."""
        return self._gear_table()

    @property
    def gear_survey_running(self) -> bool:
        """True while a gearbox position is being measured: the hold that
        keeps the coupling-ratio check from acting."""
        return self.gear_survey is not None and not self.gear_survey.done

    def start_gear_survey(self, position: str) -> Optional[str]:
        """Start measuring `position`'s factor. Returns the reason it cannot
        start, or None when it has."""
        if self.survey is not None and not self.survey.done:
            return "An overshoot measurement is running. Stop it first."
        if self._binding() is None:
            return "Set the Z scale and the leadscrew first."
        table = self._gear_table()
        why = gear_mod.refusal_for(table, position)
        if why:
            return why
        self.gear_survey = gear_mod.GearSurvey(position, table)
        log.info(f"gear {position} measurement started")
        # The lever is moved to `position` for this: the place a thread
        # reference that no longer holds is cleared.
        self._on_lever_change(position)
        self._cue(self.gear_survey.message)
        return None

    def on_gear_pass(self, v: Verdict,
                     reading: Optional[gear_mod.PassReading]) -> None:
        """A pass ended; `v` is the coupling check's final verdict for it and
        `reading` the measurement's own whole-pass reading (None without one)."""
        s = self.gear_survey
        if s is None or s.done:
            return
        if reading is not None and reading.r is not None:
            what = (f"r {reading.r:.4f} over {reading.counts} counts, "
                    f"{reading.polls} polls")
        else:
            what = f"not measured ({reading.why if reading else 'no reading'})"
        log.info(f"gear {s.position} pass: {what}; check {v.kind}, "
                 f"r_since_start {v.r_since_start}")
        self._cue(s.add(v, reading))
        if s.done:
            self._on_gear_result(s)

    def cancel_gear_survey(self) -> None:
        if self.gear_survey is not None and not self.gear_survey.done:
            log.info(f"gear {self.gear_survey.position} measurement cancelled "
                     f"after {len(self.gear_survey.readings)} passes")
        self.gear_survey = None
        self._cue("")

    def save_gear_survey(self) -> Optional[str]:
        """Write the proposed factor into the gearbox table. Returns the
        reason it was not saved, or None."""
        s = self.gear_survey
        if s is None or s.state != gear_mod.PROPOSED:
            return "No gear factor to save."
        if self._gear_table() != s.table:
            return "Gearbox table changed during the measurement. Run it again."
        try:
            text = s.updated_table().text()
        except (GearTableError, ValueError) as e:
            return f"Factor not saved: {e}"[:65]
        if not self._apply_gear_table(text):
            return "Gearbox table not changed. See the status bar."
        log.info(f"gear {s.position} factor saved: {text}")
        table = s.table
        later = table.names[table.names.index(s.position) + 1:]
        self.gear_survey = None
        self._cue(survey_mod.NEXT_SETTING if later else "")
        return None
