import collections
import time

from fractions import Fraction

from kivy.logger import Logger
from kivy.properties import StringProperty, NumericProperty, BooleanProperty

from reflex.dispatchers.saving_dispatcher import SavingDispatcher
from reflex.utils.ctype_calc import uint32_subtract_to_int32

log = Logger.getChild(__name__)


class ServoDispatcher(SavingDispatcher):
    _save_class_name = "ServoBar"

    name = StringProperty("R")
    maxSpeed = NumericProperty(1000)
    acceleration = NumericProperty(1000)
    speed = NumericProperty(0)
    jogSpeed = NumericProperty(0)
    ratioNum = NumericProperty(400)
    ratioDen = NumericProperty(360)
    offset = NumericProperty(0.0)
    divisions = NumericProperty(12)
    preferredDirection = NumericProperty(1)
    index = NumericProperty(0)

    servoMode = NumericProperty(0)
    #: True only while servoMode is being assigned from a firmware
    #: readback. Deliberately a plain attribute, not a Kivy property:
    #: it must not itself generate change events.
    servoMode_from_firmware = False
    unitsPerTurn = NumericProperty(360.0)
    oldOffset = NumericProperty(0.0)

    elsMode = BooleanProperty(False)
    leadScrewPitch = NumericProperty(0.25)
    leadScrewPitchIn = BooleanProperty(True)
    leadScrewPitchSteps = BooleanProperty(800)
    reverse = BooleanProperty(False)

    position = NumericProperty(0)
    scaledPosition = NumericProperty(0)
    formattedPosition = StringProperty("--")

    disableControls = BooleanProperty(False)

    _skip_save = [
        "update_tick",
        "connected",
        "device",
        "position",
        "scaledPosition",
        "formattedPosition",
        "servoMode",
        "oldOffset",
        "offset",
        "index",
        "preferredDirection",
        "disableControls",
        "speed",
        "stepsToGo",
    ]

    def __init__(self, board, formats, **kv):
        self.board = board
        self.formats = formats
        super().__init__(**kv)
        self.configure_lead_screw_ratio(self, None)

        # Board event bindings
        self.board.bind(connected=self.on_connected)
        self.board.bind(connected=self.update_positions)
        self.board.bind(update_tick=self.on_update_tick)

        # Property bindings
        self.bind(divisions=self.update_positions)
        self.bind(ratioNum=self.update_positions)
        self.bind(ratioDen=self.update_positions)
        self.bind(ratioNum=self.update_scaledPosition)
        self.bind(ratioDen=self.update_scaledPosition)
        self.bind(position=self.update_scaledPosition)
        self.bind(elsMode=self.update_scaledPosition)
        self.bind(reverse=self.update_scaledPosition)
        self.formats.bind(current_format=self.update_scaledPosition)
        self.update_scaledPosition(self, None)

        self.bind(leadScrewPitch=self.configure_lead_screw_ratio)
        self.bind(leadScrewPitchIn=self.configure_lead_screw_ratio)
        self.bind(leadScrewPitchSteps=self.configure_lead_screw_ratio)
        self.bind(reverse=self._on_reverse_changed)

        # Private variables that don't need dispatchers etc
        self.encoderPrevious = 0
        self.encoderCurrent = 0
        self.previous_axis_time = time.time()
        self.speed_history = collections.deque(maxlen=4)
        self.previousIndex = 0
        self.step_positions = dict()
        self.positions = dict()
        self.disableControls = True
        # Watchdog state. None = nothing commanded yet this session, so there is
        # nothing to compare the firmware against; set on the first UI-originated
        # servoMode write. Deliberately NOT initialised to 0 -- that would make a
        # firmware value retained across a UI restart look like a divergence on
        # the first poll, before the app has asked for anything.
        self._commanded_servo_mode = None
        self._divergence_polls = 0
        self.servoMode = 0

    def configure_lead_screw_ratio(self, instance, value):
        if self.elsMode is True:
            leadScrewPitch = Fraction(self.leadScrewPitch)

            if self.leadScrewPitchIn is True:
                leadScrewPitch = leadScrewPitch * Fraction(254, 10)

            leadScrewRatio = leadScrewPitch * Fraction(1, self.leadScrewPitchSteps)
            self.ratioNum = leadScrewRatio.numerator
            self.ratioDen = leadScrewRatio.denominator

    def on_connected(self, instance, value):
        try:
            if self.board.connected:
                self.encoderPrevious = self.board.fast_data_values['servoCurrent']
                self.encoderCurrent = self.board.fast_data_values['servoCurrent']
                # ADOPTION IS AN OBSERVATION. The firmware retains servoMode
                # across a UI restart, so at connect this mirrors whatever the
                # machine already happens to be doing -- the operator has not
                # asked for anything. Flagged exactly like the poll mirror in
                # on_update_tick (see its comment): unflagged, on_servoMode
                # would record the observed value into _commanded_servo_mode
                # (if the UI commanded 0 earlier this session and a
                # reconnected firmware reports nonzero, the watchdog SHOULD
                # see that divergence, not have its baseline moved to match),
                # and the servoMode->syncEnable binding in dispatchers/els.py
                # would write sync to the spindle scale from a stale retained
                # value.
                self.servoMode_from_firmware = True
                try:
                    self.servoMode = self.board.fast_data_values['servoMode']
                finally:
                    self.servoMode_from_firmware = False
                self.board.device['servo']['maxSpeed'] = self.maxSpeed
                self.board.device['servo']['acceleration'] = self.acceleration
                # jogSpeed belongs in this re-push for the same reason as its
                # three siblings: firmware RAM does not survive a reset, and
                # nothing else rewrites it at connect.
                #
                # THE EXPOSURE IS NARROW, and worth stating accurately rather
                # than inflating. It is largely self-healing: jogbar.py zeroes
                # jogSpeed on every jog release, so the next press re-sends it.
                # The one case that does not heal is a firmware reset landing
                # while a jog button is HELD -- there is no False->True edge on
                # enable_jog left to re-trigger the write, so firmware keeps its
                # freshly-zeroed jogSpeed and the jog looks dead for the rest of
                # that hold, until the operator releases and presses again.
                self.board.device['servo']['jogSpeed'] = self.jogSpeed
                servo_dir = -1 if self.reverse else 1
                self.board.device['servo']['servoDir'] = servo_dir

                if self.servoMode == 0:
                    self.disableControls = True
                else:
                    self.disableControls = False
        except Exception as e:
            log.error(str(e))

    def update_positions(self, *args, **kv):
        ratio = Fraction(self.ratioNum, self.ratioDen)
        if self.divisions < 1:
            self.divisions = 1
        self.positions = dict()
        self.step_positions = dict()
        for i in range(self.divisions):
            self.positions[i] = i * (self.unitsPerTurn / self.divisions)
            self.step_positions[i] = round(self.positions[i] / ratio)

        self.previousIndex = 0
        self.index = 0

    def on_update_tick(self, instance, value):
        try:
            if not self.board.connected:
                return

            self.encoderPrevious = self.encoderCurrent
            self.encoderCurrent = self.board.fast_data_values['servoCurrent']
            servoMode = self.board.fast_data_values['servoMode']
            if servoMode != self.servoMode:
                # Flagged so listeners can tell an OBSERVED value from a
                # COMMANDED one. This assignment mirrors what the firmware
                # reports; it is not the operator asking for anything, and a
                # listener that writes hardware in response turns a readback
                # into a command. dispatchers/els.py checks this before
                # driving syncEnable — see the note there for the disengage
                # latch it closes.
                self.servoMode_from_firmware = True
                try:
                    self.servoMode = servoMode
                finally:
                    self.servoMode_from_firmware = False

            self._check_servo_mode_divergence(servoMode)

            steps_per_second = self.board.fast_data_values['servoSpeed']
            self.speed_history.append(steps_per_second)
            speed = sum(self.speed_history) / len(self.speed_history)
            if speed != self.speed:
                self.speed = speed

            delta = uint32_subtract_to_int32(self.encoderCurrent, self.encoderPrevious)
            self.position += delta
            if (
                    self.board.fast_data_values['stepsToGo'] == 0 and
                    self.servoMode != 0 and
                    self.disableControls
                    and self.board.connected
            ):
                log.info("Disable Controls False")
                self.disableControls = False
        except Exception as e:
            log.error(f"Unable to read servo: {str(e)}")

    # Consecutive disagreeing polls before saying anything. Above 1 so a write
    # still in flight -- commanded here, not yet acknowledged there -- is not
    # reported as a divergence.
    SERVO_MODE_DIVERGENCE_POLLS = 5

    def _check_servo_mode_divergence(self, observed):
        """LOG-ONLY watchdog: did the machine keep feeding after we said stop?

        The invariant: nothing an operator can legitimately do makes the firmware
        disagree with the last servoMode the UI wrote. Any persistent
        disagreement is a defect somewhere -- a dropped write, a firmware task
        re-asserting on its own (servoEnableTask does exactly this, Ramps.c
        approx 1105), or a path that cleared elsStop.enable while sync was still
        live. This does not care which; it reports the disagreement.

        WHY IT ONLY LOGS, and why that is not timidity. Faulting to alarm would
        call on_enter_alarm, which calls set_enable(False) -- and clearing enable
        releases the carriage hold (Ramps.c:815/826). A false positive during a
        live pass would therefore TRIGGER the very hazard this exists to detect.
        Escalating to alarm is only safe once the disengage path is provably
        safe, and it is not yet: see the branch notes and the Open Loops task.

        Only the dangerous direction is reported. "We said run, it says stopped"
        is a stalled feed -- annoying, visible, and not a runaway.
        """
        commanded = self._commanded_servo_mode
        if commanded is None:
            return          # nothing commanded yet this session; nothing to compare
        if not (commanded == 0 and observed != 0):
            if self._divergence_polls:
                log.warning(
                    f"servoMode divergence CLEARED after "
                    f"{self._divergence_polls} polls (now {observed})"
                )
            self._divergence_polls = 0
            return

        self._divergence_polls += 1
        if self._divergence_polls == self.SERVO_MODE_DIVERGENCE_POLLS:
            # Once per episode, not once per poll -- a watchdog that floods the
            # log is a watchdog nobody reads, and this needs to be findable
            # afterwards rather than loud at the time.
            log.warning(
                f"servoMode DIVERGENCE: UI commanded 0, firmware reports "
                f"{observed} for {self._divergence_polls} consecutive polls. "
                f"The carriage may be driven without the UI asking. See ELS "
                f"disengage/reconnect notes."
            )

    def update_scaledPosition(self, instance, value):
        ratio = Fraction(self.ratioNum, self.ratioDen)

        is_rotary = self.elsMode is False and self.unitsPerTurn > 0
        if is_rotary:
            val = float(self.position * ratio)
            self.scaledPosition = val % self.unitsPerTurn
            fmt = self.formats.angle_format
        else:
            self.scaledPosition = float(self.position * ratio) * self.formats.factor
            fmt = self.formats.position_format

        fp = fmt.format(self.scaledPosition)
        if fp != self.formattedPosition:
            self.formattedPosition = fp

    def go_next(self):
        self.preferredDirection = 1
        self.index = (self.index + 1) % self.divisions

    def go_previous(self):
        self.preferredDirection = -1
        self.index = (self.index - 1) % self.divisions

    def on_index(self, instance, value):
        ratio = Fraction(self.ratioNum, self.ratioDen)
        self.index = self.index % self.divisions

        index_delta = (self.index - self.previousIndex)
        half_divisions = self.divisions // 2
        steps_per_turn = (self.unitsPerTurn / ratio)
        delta = self.step_positions[self.index] - self.step_positions[self.previousIndex]

        if self.preferredDirection > 0:
            if index_delta > half_divisions:
                delta = -(steps_per_turn - delta)
            if index_delta <= -half_divisions:
                delta = (delta + steps_per_turn)

        if self.preferredDirection < 0:
            if index_delta >= half_divisions:
                delta = -(steps_per_turn - delta)
            if index_delta < -half_divisions:
                delta = (delta + steps_per_turn)

        if delta != 0:
            self.board.device['servo']['stepsToGo'] = delta
            self.disableControls = True
            self.previousIndex = self.index

    def on_offset(self, instance, value):
        ratio = Fraction(self.ratioNum, self.ratioDen)
        delta = value - self.oldOffset
        delta_steps = int(delta / ratio)
        if delta_steps != 0:
            self.board.device['servo']['stepsToGo'] = delta_steps
            self.disableControls = True
            self.oldOffset = value

    def on_maxSpeed(self, instance, value):
        self.board.device['servo']['maxSpeed'] = self.maxSpeed

    def set_max_speed(self, value):
        # Write maxSpeed to the hardware without touching the stored property,
        # so callers can temporarily override and later restore via
        # set_max_speed(self.maxSpeed).
        if self.board.connected:
            self.board.device['servo']['maxSpeed'] = value

    def on_jogSpeed(self, instance, value):
        self.board.device['servo']['jogSpeed'] = self.jogSpeed

    def on_acceleration(self, instance, value):
        self.board.device['servo']['acceleration'] = self.acceleration

    def _on_reverse_changed(self, instance, value):
        if not self.board.connected:
            return
        servo_dir = -1 if self.reverse else 1
        try:
            self.board.device['servo']['servoDir'] = servo_dir
        except Exception as e:
            log.error(f"Unable to write servoDir: {e}")

    def on_servoMode(self, instance, value):
        self.board.device['fastData']['servoMode'] = self.servoMode
        if not self.servoMode_from_firmware:
            # Remember what WE asked for. Only UI-originated changes count as
            # commands; a value mirrored back from the firmware is an
            # observation, and recording it here would make the watchdog below
            # compare the firmware against itself -- always agreeing, never able
            # to fail. That is the whole distinction this watchdog rests on.
            self._commanded_servo_mode = self.servoMode
        if self.servoMode != 0:
            log.info("Disable Controls False")
            self.disableControls = False
        else:
            log.info("Disable Controls True")
            self.disableControls = True

    def toggle_enable(self):
        if not self.board.connected:
            self.servoMode = 0
            return

        if self.servoMode != 0:
            self.servoMode = 0
        else:
            self.servoMode = 1

    def stop_feed(self):
        """Force the sync feed off (servoMode=0), idempotent. Used as a safety
        stop when ELS disengages so a running spindle-synced feed doesn't keep
        driving the carriage with no ELS stop to arrest it."""
        if self.servoMode != 0:
            self.servoMode = 0

    def set_current_position(self, value):
        ratio = Fraction(self.ratioNum, self.ratioDen)
        self.position = int(value / ratio)

    def update_current_position(self):
        from reflex.components.popups.keypad import Keypad
        keypad = Keypad()
        keypad.show_with_callback(self.set_current_position, self.scaledPosition)
