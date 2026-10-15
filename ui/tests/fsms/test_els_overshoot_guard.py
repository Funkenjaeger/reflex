"""What decides whether the stop-overshoot correction may apply (OvershootGuard).

Pins: the one-time migration (only for the setting as read at startup, only at
the geometry the built-in table was measured under); a stale table switches the
correction off once, keeps the table, and applies again when the binding is
restored; the outcome watch (a corrected stop more than one count past turns
the correction off, anything else is a readout); and the measurement (the
correction is held off while it runs, stops go to it and not to the watch, a
fitted sweep saves as a measured table only if the settings did not change).
"""
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")

import datetime
from fractions import Fraction
from types import SimpleNamespace

from reflex.fsms import els_overshoot_guard as g
from reflex.fsms import els_overshoot_table as tbl
from reflex.fsms import els_overshoot_survey as sv
from reflex.fsms.els_overshoot import DEVELOPMENT_LATHE_TABLE
from reflex.fsms.els_overshoot_table import Binding
from reflex.fsms.els_stop_outcome import StopOutcome
from reflex.utils.notices import NOTICE_INFO, NOTICE_WARNING

DEV = Binding(Fraction(1, 200), Fraction(127, 64000), "A=1, B=2, C=1/2")
OTHER_SCALE = Binding(Fraction(1, 400), DEV.mm_per_step, DEV.gearbox)
OTHER_GEARBOX = Binding(DEV.mm_per_count, DEV.mm_per_step, "A=1, B=3, C=1/2")
TODAY = datetime.date(2026, 10, 11)


class Rig:
    def __init__(self, correction, live=DEV, table_from=None, gear=""):
        self.els = SimpleNamespace(
            els_overshoot_correction=correction, els_overshoot_table="",
            els_overshoot_mm_per_count="", els_overshoot_mm_per_step="",
            els_overshoot_gearbox="", els_overshoot_source="")
        if table_from is not None:
            for k, v in tbl.persisted(tbl.migration(table_from)).items():
                setattr(self.els, k, v)
        self.live = [live]
        self.notices = []
        self.cues = []
        self.results = []
        self.gear_name = gear
        self.guard = g.OvershootGuard(
            self.els, lambda: self.live[0], self._notify,
            gear=lambda: self.gear_name, today=lambda: TODAY,
            cue=self.cues.append, on_result=self.results.append)

    def _notify(self, message, severity, seconds=None):
        self.notices.append((message, severity, seconds))

    def texts(self, severity=None):
        return [m for m, s, _ in self.notices if severity is None or s == severity]

    def stored_text(self):
        return self.els.els_overshoot_table


def out(past=0, offset=3, steps=0, rate=1200.0, coast=20):
    """A stop landing `past` counts past its target (negative: short)."""
    return StopOutcome(seq=1, offset=offset, rate=rate, stop_position=1000,
                       trigger_z=1000 + past - coast, settled_z=1000 + past,
                       direction=1, steps_to_go=steps)


# ─── migration ───────────────────────────────────────────────────────────

def test_migrates_the_built_in_table_once_at_the_geometry():
    r = Rig(True)
    r.guard.check()
    assert r.els.els_overshoot_source == "migrated"
    assert r.els.els_overshoot_correction is True
    assert r.notices == [(g.MIGRATED_NOTICE, NOTICE_INFO, None)]
    assert r.guard.table_in_counts() == DEVELOPMENT_LATHE_TABLE
    r.guard.check()
    assert len(r.notices) == 1


def test_other_geometry_turns_the_correction_off_and_stores_nothing():
    r = Rig(True, live=OTHER_SCALE)
    r.guard.check()
    assert r.els.els_overshoot_correction is False
    assert r.notices == [(g.NOT_THIS_MACHINE_NOTICE, NOTICE_WARNING, None)]
    assert r.stored_text() == ""


def test_switching_on_later_with_no_table_is_refused_even_at_dev_geometry():
    r = Rig(False)
    r.guard.check()
    r.els.els_overshoot_correction = True
    r.guard.check()
    assert r.els.els_overshoot_correction is False
    assert r.notices == [(g.NO_TABLE_NOTICE, NOTICE_WARNING, None)]
    assert r.stored_text() == ""


def test_unprovisioned_live_binding_does_nothing():
    r = Rig(True, live=None)
    r.guard.check()
    assert r.notices == [] and r.els.els_overshoot_correction is True
    assert r.stored_text() == ""
    assert r.guard.table_in_counts() is None


def test_migration_waits_for_the_binding_to_exist():
    r = Rig(True, live=None)
    r.guard.check()
    r.live[0] = DEV
    r.guard.check()
    assert r.els.els_overshoot_source == "migrated"


# ─── stale ───────────────────────────────────────────────────────────────

def test_stale_table_turns_the_correction_off_once_and_is_kept():
    r = Rig(True, table_from=DEV)
    before = r.stored_text()
    assert r.guard.table_in_counts() is not None
    r.live[0] = OTHER_GEARBOX
    r.guard.check()
    assert r.els.els_overshoot_correction is False
    assert r.notices == [("Gearbox table changed. Overshoot correction is off.",
                          NOTICE_WARNING, None)]
    assert r.stored_text() == before
    assert r.guard.table_in_counts() is None
    r.guard.check()
    assert len(r.notices) == 1


def test_stale_table_is_not_applied_even_if_switched_on_again():
    r = Rig(True, table_from=DEV)
    r.live[0] = OTHER_SCALE
    r.guard.check()
    r.els.els_overshoot_correction = True
    assert r.guard.table_in_counts() is None
    assert r.guard.stale_reason() == "Z scale changed"
    assert r.guard.status_text().startswith("Stale: z scale changed")


def test_restoring_the_binding_makes_the_table_usable_again():
    r = Rig(True, table_from=DEV)
    r.live[0] = OTHER_GEARBOX
    r.guard.check()
    r.live[0] = DEV
    r.els.els_overshoot_correction = True
    n = len(r.notices)
    r.guard.check()
    assert r.els.els_overshoot_correction is True
    assert len(r.notices) == n
    assert r.guard.table_in_counts() == DEVELOPMENT_LATHE_TABLE


def test_restart_with_a_matching_binding_applies_the_table():
    r = Rig(True, table_from=DEV)
    r.guard.check()
    assert r.notices == []
    assert r.guard.table_in_counts() == DEVELOPMENT_LATHE_TABLE


def test_correction_off_gives_no_table():
    r = Rig(False, table_from=DEV)
    assert r.guard.table_in_counts() is None


# ─── the outcome watch ───────────────────────────────────────────────────

def watch_rig():
    r = Rig(True, table_from=DEV)
    r.guard.check()
    return r


def test_corrected_stop_past_by_two_turns_the_correction_off():
    r = watch_rig()
    r.guard.on_outcome(out(past=2), 1.0)
    assert r.els.els_overshoot_correction is False
    (msg, sev, secs), = r.notices
    assert sev == NOTICE_WARNING and "past" in msg and secs == g.READOUT_SECONDS


def test_corrected_stop_past_by_one_is_inside_tolerance_and_silent():
    r = watch_rig()
    r.guard.on_outcome(out(past=1), 1.0)
    assert r.els.els_overshoot_correction is True
    assert r.notices == []
    assert "past" in r.guard.last_landing


def test_corrected_stop_short_shows_nothing_but_is_kept_for_measure():
    """first run on the development lathe, 2026-10-11: a readout on every corrected stop is
    distracting in normal use. A short landing is silent; the Measure window
    shows the last one."""
    r = watch_rig()
    r.guard.on_outcome(out(past=-3), 1.0)
    assert r.els.els_overshoot_correction is True
    assert r.notices == [] and r.cues == []
    assert r.guard.last_landing == "Stop landed 3 counts (0.015 mm) short."


def test_uncorrected_stop_gives_no_notice():
    r = watch_rig()
    r.guard.on_outcome(out(past=5, offset=0), 1.0)
    assert r.notices == [] and r.els.els_overshoot_correction is True


def test_stop_with_steps_queued_is_not_judged():
    r = watch_rig()
    r.guard.on_outcome(out(past=5, steps=3), 1.0)
    assert r.notices == [] and r.els.els_overshoot_correction is True


def test_readout_uses_the_display_factor():
    r = watch_rig()
    r.guard.on_outcome(out(past=-2), 10 / 254)
    assert "in)" in r.guard.last_landing


# ─── the measurement ─────────────────────────────────────────────────────

def test_start_survey_needs_a_live_binding():
    r = Rig(True, live=None)
    reason = r.guard.start_survey(sv.DETECT)
    assert isinstance(reason, str) and reason
    assert r.guard.survey is None


def test_survey_holds_the_correction_off_until_cancelled():
    r = Rig(True, table_from=DEV)
    assert r.guard.table_in_counts() is not None
    assert r.guard.start_survey(sv.DETECT) is None
    assert r.guard.table_in_counts() is None
    assert r.els.els_overshoot_correction is True
    r.guard.cancel_survey()
    assert r.guard.survey is None
    assert r.guard.table_in_counts() == DEVELOPMENT_LATHE_TABLE


def test_outcomes_go_to_the_survey_cue_not_the_watch():
    """Survey lines go to the ELS bar cue strip, not to a timed notice
    (first run on the development lathe, 2026-10-11: a cue on a timer was waited out, or lost)."""
    r = Rig(True, table_from=DEV)
    r.guard.start_survey(sv.DETECT)
    assert r.cues == [r.guard.survey.message]
    r.guard.on_outcome(out(past=5, coast=3), 1.0)        # borderline: asks again
    assert r.els.els_overshoot_correction is True
    assert r.notices == []
    assert r.cues[-1] == r.guard.survey.message
    assert r.cues[-1].endswith("Proceed with the next pass.")


def test_cancel_and_save_clear_the_cue():
    r = Rig(True, table_from=DEV)
    r.guard.start_survey(sv.DETECT)
    r.guard.cancel_survey()
    assert r.cues[-1] == ""
    r.guard.start_survey(sv.SWEEP)
    feed_sweep(r)
    assert r.guard.save_survey() is None
    assert r.cues[-1] == ""


def test_a_finished_sweep_opens_its_result_and_a_detection_does_not():
    """first run on the development lathe, 2026-10-11: at the end of the sweep the app opens
    the window itself."""
    r = Rig(True, table_from=DEV)
    r.guard.start_survey(sv.DETECT)
    r.guard.on_outcome(out(coast=9, offset=0), 1.0)
    assert r.guard.survey.state == sv.YES and r.results == []
    r.guard.start_survey(sv.SWEEP)
    feed_sweep(r)
    assert r.guard.survey.state == sv.FITTED
    assert r.results == [r.guard.survey]
    r.guard.on_outcome(out(coast=9, offset=0), 1.0)      # after the end: nothing more
    assert len(r.results) == 1


def feed_sweep(r, coasts=((3, 3, 4), (9, 8, 9), (28, 29, 29), (42, 43, 41)),
               rates=(600.0, 1200.0, 2400.0, 3500.0)):
    for rate, cs in zip(rates, coasts):
        for c in cs:
            r.guard.on_outcome(out(coast=c, offset=0, rate=rate), 1.0)


def test_save_stores_a_measured_table_without_touching_the_setting():
    r = Rig(False, gear="B")
    r.guard.start_survey(sv.SWEEP)
    feed_sweep(r)
    assert r.guard.survey.state == sv.FITTED
    assert r.guard.save_survey() is None
    assert r.els.els_overshoot_source == "measured 2026-10-11 in B"
    assert r.els.els_overshoot_correction is False
    assert r.guard.survey is None
    saved = tbl.load(r.els.els_overshoot_table, r.els.els_overshoot_mm_per_count,
                     r.els.els_overshoot_mm_per_step, r.els.els_overshoot_gearbox,
                     r.els.els_overshoot_source)
    assert saved.binding == DEV
    assert saved.points[-1] == (Fraction("17.5"), Fraction("0.215"))


def test_save_without_a_gear_name_omits_the_in_clause():
    r = Rig(True)
    r.guard.start_survey(sv.SWEEP)
    feed_sweep(r)
    assert r.guard.save_survey() is None
    assert r.els.els_overshoot_source == "measured 2026-10-11"
    assert r.els.els_overshoot_correction is True


def test_save_refuses_when_the_settings_changed_during_the_sweep():
    r = Rig(False)
    r.guard.start_survey(sv.SWEEP)
    feed_sweep(r)
    r.live[0] = OTHER_GEARBOX
    reason = r.guard.save_survey()
    assert isinstance(reason, str) and reason
    assert r.stored_text() == ""


def test_save_refuses_without_a_fitted_result():
    r = Rig(False)
    assert isinstance(r.guard.save_survey(), str)          # no survey at all
    r.guard.start_survey(sv.SWEEP)
    assert isinstance(r.guard.save_survey(), str)          # still running
    r.guard.cancel_survey()
    r.guard.start_survey(sv.SWEEP)
    feed_sweep(r, coasts=((3, 3, 9),) + ((9, 9, 9),) * 3)  # refused: spread
    assert r.guard.survey.state == sv.REFUSED
    assert isinstance(r.guard.save_survey(), str)
    assert r.stored_text() == ""
