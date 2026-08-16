from reflex.utils.base_device import BaseDevice, TypeDefinition
from reflex.utils import communication
SCALES_COUNT = 4
SERVOS_COUNT = 3

TimHandleTypeDef = TypeDefinition(
    name = "TIM_HandleTypeDef",
    length = 2,
    struct_unpack_string = "L",
    read_function = communication.read_long,
    write_function = communication.write_long,
)

Int16 = TypeDefinition(
    name = "int16_t",
    length = 1,
    struct_unpack_string = "h",
    read_function = communication.read_signed,
    write_function = communication.write_signed,
)

UInt16 = TypeDefinition(
    name ="uint16_t",
    length=1,
    struct_unpack_string="H",
    read_function=communication.read_unsigned,
    write_function=communication.write_unsigned,
)

Bool = TypeDefinition(
    name="bool",
    length=1,
    struct_unpack_string="H",
    read_function=communication.read_unsigned,
    write_function=communication.write_unsigned
)

Uint32T = TypeDefinition(
    name="uint32_t",
    length=2,
    struct_unpack_string="L",
    read_function=communication.read_long,
    write_function=communication.write_long
)

Int32 = TypeDefinition(
    name="int32_t",
    length=2,
    struct_unpack_string="l",
    read_function=communication.read_long,
    write_function=communication.write_long
)

Float = TypeDefinition(
    name="float",
    length=2,
    struct_unpack_string="f",
    read_function=communication.read_float,
    write_function=communication.write_float
)


class Servo(BaseDevice):
    definition = """
typedef struct {
  float maxSpeed;
  float currentSpeed;
  float jogSpeed;
  float acceleration;
  int32_t stepsToGo;
  uint32_t destinationSteps;
  uint32_t currentSteps;
  uint32_t desiredSteps;
  int16_t servoDir;
  int16_t _pad;
} servo_t;
"""


class Scale(BaseDevice):
    definition = """
typedef struct {
  uint32_t timerHandleSlot;
  int32_t position;
  int32_t speed;
  int32_t syncRatioNum, syncRatioDen;
  uint16_t syncEnable;
  int16_t scaleDir;
} input_t;
"""


class FastData(BaseDevice):
    definition = """
typedef struct {
  uint32_t servoCurrent;
  uint32_t servoDesired;
  uint32_t stepsToGo;
  float servoSpeed;
  int32_t scaleCurrent[4];
  int32_t scaleSpeed[4];
  uint32_t cycles;
  uint32_t executionInterval;
  uint16_t servoMode;
  uint16_t _pad0;
} fastData_t;
"""

class ElsStop(BaseDevice):
    """Mirror of ``elsStop_t`` in reflex-fw ``Core/Inc/Ramps.h``.

    MUST match the firmware struct byte for byte — the whole shared struct is
    memory-mapped straight onto Modbus holding registers with no translation
    layer, so a field added on one side and not the other silently reinterprets
    every register after it. ``tests/test_register_map_contract.py`` parses the
    firmware header and diffs it against this string; that test failing is the
    intended alarm, not a nuisance.

    Field semantics, units and sign conventions are documented at the firmware
    struct definition and in ``Core/Inc/els_backlash_cal.h``; they are
    deliberately NOT duplicated here, because two copies of a unit convention is
    how they drift apart. Comments are also kept out of the typedef string
    itself — the parser consumes it verbatim.

    Calibration / take-up block (protocolVersion .. takeupThreshCounts) added
    2026-08-08 with the closed-loop backlash calibration feature; struct grew
    56 -> 96 bytes, rampsSharedData_t 264 -> 304.

    Diagnostic scratchpad (diagSchema .. diagReserved) added 2026-08-14: a fixed
    64-register block reserved so that temporary firmware instrumentation never
    has to change the layout again. struct grew 96 -> 224 bytes,
    rampsSharedData_t 304 -> 432. It is RESERVED IN EVERY BUILD but written only
    when the firmware is compiled with ELS_DIAG_SCRATCH, so in a release build
    the whole block reads zero.

    ``diagSchema`` names whichever probe is compiled in, and 0 means "nothing
    here". Anything reading this block MUST check it first and refuse to
    interpret a schema it does not recognise -- protocolVersion deliberately
    does NOT bump when a probe changes, because the layout does not change, so
    diagSchema is the only thing standing between a reader and a plausible
    number with the wrong meaning.

    Read the block ON DEMAND only. 64 registers is ~12 ms of extra serial time
    at 115200 baud against ~29 ms for the whole map -- a permanent 40% tax on
    every poll cycle for a block that is empty in production.
    """

    definition = """
typedef struct {
  uint16_t enable;
  uint16_t scaleIndex;
  int32_t  stopPosition;
  int16_t  stopDirection;
  uint16_t active;
  float    threadPitchSteps;
  int32_t  hysteresis;
  float    zCountsPerPitch;
  uint32_t backlashSteps;
  int32_t  latchedZ;
  int32_t  latchedSpindle;
  uint16_t referenceLatched;
  uint16_t takeupPending;
  float    lastIdealAdvance;
  float    lastActualAdvance;
  float    lastPhaseError;
  float    lastCorrection;
  uint16_t protocolVersion;
  uint16_t calCommand;
  uint16_t calSeq;
  uint16_t calResult;
  uint16_t takeupResult;
  uint16_t takeupSeq;
  int32_t  calMeasured[3];
  int32_t  calCeilingSteps;
  int32_t  calMotionThreshCounts;
  int32_t  lastTakeupZDelta;
  int32_t  takeupThreshCounts;
  uint16_t diagSchema;
  uint16_t diagSeq;
  uint16_t diagBucketTicks;
  uint16_t diagBucketCount;
  int32_t  diagSettleTicks;
  int32_t  diagNetCounts;
  int16_t  diagTrace[50];
  uint16_t diagCaptureTicks;
  uint16_t diagEndReason;
  uint16_t diagReserved[4];
} elsStop_t;
"""


# --- Frozen protocol constants -------------------------------------------
# Mirrored from reflex-fw Core/Inc/els_backlash_cal.h. Values are part of the
# Modbus contract; never renumber, only append.

ELS_PROTOCOL_VERSION = 2        # elsStop.protocolVersion this UI is built against

# Diagnostic scratchpad schema ids (elsStop.diagSchema). 0 means no probe is
# compiled into the firmware and the block must not be interpreted at all.
# Mirrored from reflex-fw Core/Src/Ramps.c. Never renumber, only append: a
# stale reader that recognises an old number must not silently accept a new
# probe's data under it.
ELS_DIAG_SCHEMA_NONE = 0
ELS_DIAG_SCHEMA_TAKEUP_SETTLE = 1      # RETIRED: ran past the gate's decision into the pass
ELS_DIAG_SCHEMA_TAKEUP_SETTLE_V2 = 2   # same, but the capture ends at the servo's next pulse

# elsStop.diagEndReason. A window-full capture did not finish measuring: its
# last bucket is a floor, not a result, and it must not be read as one.
ELS_DIAG_END_PULSE = 1     # servo drove again -- settling is genuinely over
ELS_DIAG_END_WINDOW = 2    # ran out of buckets first

ELS_CAL_OK = 0
ELS_CAL_ERR_ENABLED = 1         # refused: a threading job is live
ELS_CAL_ERR_SERVOMODE = 2       # refused: servoMode != 1
ELS_CAL_ERR_CONFIG = 3          # refused: ceiling or motion threshold unset
ELS_CAL_ERR_NO_MOTION = 4       # drove the full ceiling, carriage never moved
ELS_CAL_ERR_ABORTED = 5         # conditions changed mid-run

ELS_TAKEUP_ERR_UNCONFIRMED = 4  # shares NO_MOTION: same physical cause
ELS_TAKEUP_ERR_TIMEOUT = 6      # take-up never reached its commanded target

# Operator-legible causes. The take-up ones deliberately lead with the physical
# check rather than the firmware state — an operator at the machine can act on
# "is the half-nut engaged?" and cannot act on "takeupResult == 4".
ELS_CAL_MESSAGES = {
    ELS_CAL_ERR_ENABLED: "Disengage the ELS stop before calibrating.",
    ELS_CAL_ERR_SERVOMODE: "Servo is not in sync/index mode.",
    ELS_CAL_ERR_CONFIG: "Calibration limits are not configured.",
    ELS_CAL_ERR_NO_MOTION: (
        "Carriage did not move — is the half-nut engaged?"
    ),
    ELS_CAL_ERR_ABORTED: "Calibration aborted — conditions changed mid-run.",
}

ELS_TAKEUP_MESSAGES = {
    ELS_TAKEUP_ERR_UNCONFIRMED: (
        "Carriage not moving — is the half-nut engaged? "
        "The cut will not start until the backlash take-up is confirmed."
    ),
    ELS_TAKEUP_ERR_TIMEOUT: (
        "Backlash take-up did not complete. Disengage and re-engage the "
        "ELS stop to clear."
    ),
}


class Global(BaseDevice):
    root_structure = True
    definition = """
typedef struct {
  uint32_t executionInterval;
  uint32_t executionIntervalPrevious;
  uint32_t executionIntervalCurrent;
  uint32_t executionCycles;
  servo_t servo;
  input_t scales[4];
  fastData_t fastData;
  elsStop_t elsStop;
} rampsSharedData_t;
"""


def takeup_failure_text(result_code, z_delta=None, thresh_counts=None):
    """Operator-facing text for a take-up failure.

    When the firmware's derived threshold and the observed motion are both
    available, name them. "Moved 5 counts, needed 11" is a diagnosis an operator
    can act on — a partially engaged half-nut looks completely different from
    one that never engaged — whereas the bare sentence leaves them guessing
    which of the two they have.

    A NEGATIVE delta means the carriage moved the WRONG way, which is a
    different fault entirely (scale direction, or something else driving the
    carriage) and is called out as such rather than folded into "not enough".
    """
    base = ELS_TAKEUP_MESSAGES.get(result_code, "Backlash take-up failed.")
    if result_code != ELS_TAKEUP_ERR_UNCONFIRMED:
        return base
    if z_delta is None or thresh_counts is None:
        return base
    if z_delta < 0:
        return (f"{base} The carriage moved {abs(int(z_delta))} counts the WRONG "
                f"way — check the Z scale direction.")
    return f"{base} Moved {int(z_delta)} counts, needed {int(thresh_counts)}."
