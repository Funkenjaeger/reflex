/*
 * Machine-mode derivation: the mode table, pinned.
 *
 * elsDeriveMachineMode (els_machine_mode.h) is a pure function of the shared
 * register struct — rung 1 of the 2026-08-16 architecture direction: firmware
 * states what the machine is doing; the UI compares instead of inferring.
 *
 * Two things are under test, and they are different:
 *
 *  1. THE WIRE VALUES. ELS_MMODE_* are a contract the moment a mode-watch
 *     build is flashed — reflex-ui mirrors the numbers. Pinned as LITERALS,
 *     not macro-vs-macro, so renumbering costs a deliberate edit on both
 *     sides (same discipline as the diag schema ids).
 *
 *  2. THE PRIORITY. Most-specific first: CAL and TAKEUP own the servo while
 *     they run; a hold with a commanded move outstanding IS a move (retract
 *     happens while held — the hold gates sync, not commanded motion); a
 *     hold otherwise dominates everything including jog and a feed-off
 *     servoMode, because it is the safety-relevant fact.
 *
 * No ISR, no stubs: the function is pure and this test never links Ramps.c.
 */

extern "C" {
#include "Ramps.h"
}

#include <cstdio>
#include <cstring>

static int failures = 0;

static void expect(const rampsSharedData_t *s, uint16_t calRunning,
                   uint16_t want, const char *label) {
    uint16_t got = elsDeriveMachineMode(s, calRunning);
    printf("[%s] %s -> %u (want %u)\n",
           got == want ? "PASS" : "FAIL", label, (unsigned)got, (unsigned)want);
    if (got != want) failures++;
}

int main() {
    printf("=== machine mode derivation table ===\n");

    /* The wire contract: literal values, pinned once. */
    bool wire = ELS_MMODE_OFF == 0u && ELS_MMODE_IDLE == 1u
             && ELS_MMODE_FEEDING == 2u && ELS_MMODE_MOVING == 3u
             && ELS_MMODE_JOG == 4u && ELS_MMODE_HELD == 5u
             && ELS_MMODE_TAKEUP == 6u && ELS_MMODE_CAL == 7u;
    printf("[%s] wire values 0..7 unchanged\n", wire ? "PASS" : "FAIL");
    if (!wire) failures++;

    rampsSharedData_t s;

    /* Boot: everything zero. */
    std::memset(&s, 0, sizeof(s));
    expect(&s, 0, ELS_MMODE_OFF, "all-zero boot state is OFF");

    /* Servo on, nothing to do. */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 1;
    expect(&s, 0, ELS_MMODE_IDLE, "servo on, no sync, no move: IDLE");

    /* Power feed / cutting: sync armed, not held. */
    s.scales[0].syncEnable = 1;
    expect(&s, 0, ELS_MMODE_FEEDING, "servo on + sync: FEEDING");

    /* Sync armed on any scale counts, not just the spindle slot. */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 1;
    s.scales[3].syncEnable = 1;
    expect(&s, 0, ELS_MMODE_FEEDING, "sync on a non-spindle scale: FEEDING");

    /* Commanded move (retract / indexing). */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 1;
    s.servo.stepsToGo = -300;
    expect(&s, 0, ELS_MMODE_MOVING, "stepsToGo outstanding: MOVING");

    /* Held at the stop, idle (armed-idle OR stop-fired: not distinguishable
     * today — review §4.1; the merged state is published honestly). */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 1;
    s.elsStop.enable = 1;
    s.elsStop.active = 1;
    s.scales[0].syncEnable = 1;
    expect(&s, 0, ELS_MMODE_HELD, "enable+active, no move: HELD (beats FEEDING)");

    /* Retract: a commanded move issued WHILE held. The hold gates sync, not
     * commanded motion, so the machine is moving and must say so. */
    s.servo.stepsToGo = -500;
    expect(&s, 0, ELS_MMODE_MOVING, "commanded move while held: MOVING (beats HELD)");

    /* A hold with the feed off (e.g. reconnect re-arm with the feed down)
     * is still a hold — the safety-relevant fact dominates the servo axis. */
    std::memset(&s, 0, sizeof(s));
    s.elsStop.enable = 1;
    s.elsStop.active = 1;
    expect(&s, 0, ELS_MMODE_HELD, "enable+active with servo off: HELD (beats OFF)");

    /* Jog. */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 2;
    expect(&s, 0, ELS_MMODE_JOG, "servoMode 2: JOG");

    /* Jog while ENGAGED-HELD: updateJogPosition ignores the hold, so the
     * machine is genuinely jogging and must say so. Learned on 2026-08-17:
     * with HELD ranked above JOG, a whole hardware session of jogging while
     * engaged-idle wrote zero JOG entries to the ledger. */
    s.elsStop.enable = 1;
    s.elsStop.active = 1;
    expect(&s, 0, ELS_MMODE_JOG, "jog while held: JOG (beats HELD)");

    /* Sync armed with the servo off: nothing can move (and post-F1 nothing
     * accrues either) — OFF, not a phantom FEEDING. */
    std::memset(&s, 0, sizeof(s));
    s.scales[0].syncEnable = 1;
    expect(&s, 0, ELS_MMODE_OFF, "sync armed, servo off: OFF");

    /* Takeup in flight outranks the held/moving axis it runs inside. */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 1;
    s.elsStop.enable = 1;
    s.elsStop.takeupPending = 1;
    s.servo.stepsToGo = -105;
    expect(&s, 0, ELS_MMODE_TAKEUP, "takeupPending: TAKEUP (beats MOVING)");

    /* Calibration owns the servo outright. */
    std::memset(&s, 0, sizeof(s));
    s.fastData.servoMode = 1;
    s.servo.stepsToGo = 400;
    expect(&s, 1, ELS_MMODE_CAL, "calRunning: CAL (beats everything)");

    printf("=== %s === (%d failing assertion%s)\n",
           failures == 0 ? "ALL PASS" : "FAILURES",
           failures, failures == 1 ? "" : "s");
    return failures == 0 ? 0 : 1;
}
