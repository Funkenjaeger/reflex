"""The ELS command channel, UI side: wire values and the disengage switch.

protocolVersion 13 (docs/design/els-command-channel.md). The host writes one
command id to elsStop.command; the firmware consumes it in servoEnableTask,
carries it out, writes elsStop.commandResult and bumps elsStop.commandSeq
last. commandSeq is in the tick snapshot (hot); commandResult is cold and is
read live once the seq has moved.

DISENGAGE is the only command. The firmware does the whole ordered teardown
(sync off on every scale, servoMode 0, enable 0, the job's motion cancelled),
so the UI's disengage is one write and an edge.

The values mirror fw/Core/Inc/els_command.h (ELS_CMD_* -> CMD_*), and
tests/test_registry_contract.py compares the two copies in both directions.
Append, never renumber.
"""

# Command ids (ELS_CMD_*).
CMD_NONE = 0
CMD_DISENGAGE = 1

# Outcomes (ELS_CMD_RESULT_*). Below 0x100 succeeded; 0x100 and up refused.
CMD_RESULT_OK = 0
CMD_RESULT_OK_CAL_ABORTED = 1
CMD_RESULT_ERR_UNKNOWN = 256

RESULT_NAMES = {
    CMD_RESULT_OK: "OK",
    CMD_RESULT_OK_CAL_ABORTED: "OK, calibration aborted",
    CMD_RESULT_ERR_UNKNOWN: "unknown command",
}

# Board ticks (30 Hz) to wait for commandSeq to move before calling the ack
# missing: about one second. The firmware consumes within one servoEnableTask
# tick (~100 ms) and acks on that tick or the next, so a healthy ack arrives
# in about 200 ms plus a tick of snapshot latency.
ACK_TIMEOUT_POLLS = 30


def result_is_ok(code: int) -> bool:
    """Success by range, so a code this UI does not know is still classified."""
    return int(code) < 0x100


def result_name(code: int) -> str:
    return RESULT_NAMES.get(int(code), f"code {int(code)}")
