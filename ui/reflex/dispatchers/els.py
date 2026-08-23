from kivy.logger import Logger
from kivy.properties import BooleanProperty, NumericProperty, StringProperty

from reflex.dispatchers.saving_dispatcher import SavingDispatcher

log = Logger.getChild(__name__)


class ElsDispatcher(SavingDispatcher):
    """Persists ELS axis role assignments and ELS-stop tuning."""

    _save_class_name = "Els"
    _skip_save = ["x", "y", "width", "height", "size_hint_x", "size_hint_y",
                  "pos", "size", "minimum_height", "minimum_width", "padding", "spacing",
                  "spindle_is_running"]

    # ── ELS axis roles ────────────────────────────────────────────────
    spindle_axis_index = NumericProperty(-1)
    z_axis_index = NumericProperty(-1)
    x_axis_index = NumericProperty(-1)

    # ── Reactive spindle state (updated each tick) ────────────────────
    spindle_is_running = BooleanProperty(False)

    # ── ELS stop tuning ───────────────────────────────────────────────
    # Magnitude in servo steps; 0 disables takeup. Direction is derived by
    # firmware from sign(syncRatioNum) × sign(threadPitchSteps × zCountsPerPitch).
    # User-facing entry happens in mm via the settings popup, which converts
    # using the servo ratio.
    els_backlash_steps = NumericProperty(0)

    # ── Backlash calibration (closed loop) ────────────────────────────
    # Machine-specific limits pushed to the firmware before a calibration run.
    #
    #   els_cal_ceiling_steps  — per-leg hard ceiling. Driving this far without
    #       the Z scale moving IS the open-half-nut / uncoupled failure, so it
    #       must sit comfortably past the largest credible lash but well short
    #       of anything the carriage could hit. It costs nothing to be generous:
    #       a leg ends the moment Z moves, so this only bounds the FAILURE case.
    #
    #       MEASURED ON elspi 2026-08-08: 385 steps (~0.76 mm) of real lash.
    #       That is roughly 4x the 0.05-0.20 mm range this feature was designed
    #       against, and it makes a tight ceiling actively dangerous: the first
    #       default here was 400 steps, which left EIGHTEEN steps of headroom
    #       over the measurement. A healthy machine was ~0.036 mm of lash drift
    #       away from reporting a false "carriage did not move - is the half-nut
    #       engaged?". Sized at ~2 mm now, about 2.6x the measured lash.
    #   els_cal_motion_thresh_counts — Z counts that count as real motion.
    #       On elspi (200 counts/mm) one count is ~2.5 servo steps, so 2 counts
    #       is about the floor before quantization noise. 0 makes the firmware
    #       fail CLOSED — treat it as "not commissioned", never as a default.
    els_cal_ceiling_steps = NumericProperty(1008)   # ~2.0 mm at 0.00198 mm/step
    els_cal_motion_thresh_counts = NumericProperty(2)

    # Accept/reject policy for a completed run. The three measurements must
    # agree within this spread, in servo steps, or the result is refused.
    # A wide spread means the measurement is not reproducible, which is itself
    # the finding — do NOT widen this to make a wizard proceed.
    els_cal_max_spread_steps = NumericProperty(12)

    # Take-up margin: always measured + max(pct, floor), never trimmed toward
    # the minimum. The floor exists because at a small lash a flat percentage
    # collapses into the measurement's own quantization uncertainty (~5 steps
    # at a 2-count threshold on elspi) and stops being margin at all.
    els_takeup_margin_pct = NumericProperty(20)
    els_takeup_margin_floor_steps = NumericProperty(10)

    # Last completed calibration, for display and drift comparison. Stored as
    # the raw measured mean (lash + detection distance), NOT the commanded
    # take-up — keeping them separate is what lets a later run notice drift.
    els_cal_last_measured_steps = NumericProperty(0)

    # ── Thread re-sync (manual reference latch) ───────────────────────
    # Z counts the carriage may sit from its post-jog baseline during fine
    # alignment, and how closely a hand re-seat against the drive flank must
    # return to it. DELIBERATELY tiny: a re-seat is the carriage arriving back
    # at a mechanical stop it was already against, so missing by more than a
    # couple of counts means the Z chain has lost custody of the position —
    # the same fault that would silently corrupt every ELS operation. That is
    # a finding to surface, never a reason to raise this number.
    els_resync_z_tol_counts = NumericProperty(3)

    # Calibration POLICY (spread test, take-up margin) deliberately does NOT
    # live here. ElsDispatcher cannot be constructed without a running MainApp,
    # so any logic on it can only be tested by mirroring it in a stub — which is
    # exactly how two copies of a rule drift apart. The pure functions live in
    # reflex/fsms/els_cal.py and are tested directly; this class holds only the
    # persisted configuration they read.

    # ── Re-reference notify preference ────────────────────────────────
    # How to flag when a committed ELS target's DISPLAYED value changes after a
    # DRO re-zero / coordinate-system switch (the physical target is unchanged):
    #   "silent"  — just re-render (matches the rest of the DRO); default
    #   "warn"    — amber-flag the Stop/Start fields + a small message
    #   "confirm" — an inline Keep/Reset bar
    # Persisted to Els-0.yaml like the other ELS settings.
    stop_z_reframe_notify = StringProperty("silent")

    # ── Machine direction polarity ────────────────────────────────────
    # Direction canonicalization is now handled by firmware via scaleDir
    # and servoDir. These methods return the sign for the operator's
    # "forward" toggle only — no learned/commissioned state needed.

    def direction_sign(self, els_forward: bool) -> int:
        """Sign applied to spindle syncRatioNum to drive the carriage in
        the operator's "forward" direction. ±1.
        """
        return 1 if els_forward else -1

    def stop_direction_value(self, els_forward: bool) -> int:
        """Integer value for elsStop.stopDirection register. ±1."""
        return -1 if els_forward else 1

    def __init__(self, **kwargs):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kwargs)
        self.bind(spindle_axis_index=self._apply_spindle_mode)
        # Apply on startup in case a saved value exists
        self._apply_spindle_mode()
        self.app.board.bind(update_tick=self._update_spindle_running)
        self.app.servo.bind(servoMode=self._sync_spindle_to_servo)

    def _update_spindle_running(self, *args):
        running = self.get_spindle_is_running()
        if running != self.spindle_is_running:
            self.spindle_is_running = running

    def _apply_spindle_mode(self, *args):
        """Set spindleMode=True on the selected spindle axis, False on all others."""
        idx = int(self.spindle_axis_index)
        if idx < 0:
            return
        for i, axis in enumerate(self.app.axes):
            axis.spindleMode = (i == idx)

    def get_spindle_axis(self):
        idx = int(self.spindle_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def get_z_axis(self):
        idx = int(self.z_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def get_x_axis(self):
        idx = int(self.x_axis_index)
        if 0 <= idx < len(self.app.axes):
            return self.app.axes[idx]
        return None

    def _sync_spindle_to_servo(self, instance, value):
        """Follow the servo mode with syncEnable: arm on sync feed, clear on
        everything else.

        SYNC FOLLOWS THE SYNC-FEED MODE, NOT "ANY MODE". Only servoMode == 1
        is the spindle-synced feed; 2 is jog and 0 is off. The old shape
        (`1 if value != 0 else 0`) treated any nonzero mode as feed-on, so
        merely starting jog armed the spindle scale's syncEnable — handing the
        firmware's servoEnableTask the `anySyncMotionEnabled` term it needs to
        flip the feed on by itself the moment jog ends (review 2026-08-16, F5).

        And when the mode leaves 1, clear syncEnable on ALL scales — the
        els_stop_hal.stop_sync shape — not just the spindle's. toggle_sync
        (dispatchers/axis.py, reachable from the Jog/Index coordbars) can arm
        any axis's scale, and a non-spindle scale left armed across a feed
        stop is the same firmware re-assert hazard on a different register.

        OBSERVATION MUST NOT BECOME COMMAND. servoMode is a two-way field: the UI
        writes it, and dispatchers/servo.py also assigns the POLLED FIRMWARE
        VALUE into the same property, which fires this callback. Acting on that
        turns "the firmware reports the feed is on" into "the UI commands sync
        on" — a control loop the operator is not in.

        That closed the latch behind the disengage race: firmware's
        servoEnableTask sets servoMode = 1 during the disengage window, the UI
        polls it, this callback writes syncEnable = 1 in response, and the task
        then has the `anySyncMotionEnabled` term it needs to keep asserting the
        feed forever. It is the reason the failure never recovered rather than
        lasting one poll cycle.
        """
        if not self.app.board.connected:
            return
        if getattr(self.app.servo, 'servoMode_from_firmware', False):
            log.debug(f"ignoring firmware-originated servoMode={value}; "
                      "sync state is commanded by the UI, never echoed back")
            return
        if value == 1:
            spindle_axis = self.get_spindle_axis()
            if spindle_axis is None:
                return
            inp = spindle_axis._primary_input()
            if inp is None:
                return
            self.app.board.device['scales'][inp.inputIndex]['syncEnable'] = 1
            spindle_axis.syncEnable = True
            log.info(f"Spindle syncEnable = 1 (servoMode={value})")
        else:
            # Deliberately NOT gated on a spindle axis being assigned: the
            # clear path needs no spindle, and a machine with no spindle role
            # can still have scales armed via toggle_sync.
            for scale in self.app.board.device['scales']:
                scale['syncEnable'] = 0
            for axis in self.app.axes:
                axis.syncEnable = False
            log.info(f"syncEnable cleared on all scales (servoMode={value})")

    def get_spindle_is_running(self, *args):
        speed = self.get_spindle_speed()
        return abs(speed) > 0.5
    
    def get_spindle_speed(self, *args):
        axis = self.get_spindle_axis()
        return axis.speed if axis is not None else 0.0