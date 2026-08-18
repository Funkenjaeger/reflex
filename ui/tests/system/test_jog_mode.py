"""Emulator-backed probes for the two 2026-08-17 hardware-session jog findings.

FINDING 1 — the mode-watch ledger never recorded JOG(4) all day, although the
operator jogged and the carriage moved. These tests reproduce the Jog screen
flow AT THE DISPATCHER LEVEL, exactly as the UI dispatches it (jogbar.kv's
enable button calls ServoDispatcher.toggle_enable directly; JogBar.update_jog
then does `servo.jogSpeed = ±speed; servo.servoMode = 2` — two Kivy property
writes, in that order), and then watch the FIRMWARE's fastData.servoMode
register — not the Kivy mirror — across ~2 s of pumping.

Firmware facts these tests pin (all verified against reflex-fw source):
  * The servoEnableTask re-assert (Ramps.c ~1157) fires only when
    `anySyncMotionEnabled && !elsStop.active && servoMode != 2` — mode 2 is
    EXEMPT, so firmware never rewrites an established jog. The release
    emulator build carries the re-assert LIVE (elsDiagServoGate is a no-op).
  * Post-F5 (dispatchers/els.py), entering mode 2 clears syncEnable on ALL
    scales, so the re-assert loses its `anySyncMotionEnabled` term the moment
    jog starts.
  * The ENGAGE path (els_fsm.arm_idle_stop) sets elsStop.active=1 AND
    enable=1, and els_machine_mode.h ranks HELD (enable && active) ABOVE JOG
    (servoMode == 2). updateJogPosition ignores `active`, so an engaged-idle
    machine jogs physically while the derived mode publishes HELD — the
    ledger would never show JOG. test_engaged_hold_masks_jog pins exactly
    that.

The firmware-derived machine mode is recomputed here as the same pure
function els_machine_mode.h defines (pinned in fw by els_machine_mode_test);
the release emulator does not carry the diag probe that publishes it, so the
test derives it from the registers the probe would read. calRunning is passed
as 0 — no calibration runs in these tests.

FINDING 2 — jog direction vs the commissioned servo direction
(ServoDispatcher.reverse=True → servoDir=-1 on the real lathe).
test_jog_direction_table measures all four (jogSpeed sign × servoDir)
combinations and PRINTS the observations; direction is deliberately NOT
asserted — the fix design depends on these observations. Mechanism, from
source: the pulse generator (Ramps.c ~933) chases desiredSteps, whose jog
increment sign is sign(jogSpeed) with servoDir NOT involved, so the internal
step counter (fastData.servoCurrent) always follows jogSpeed; servoDir only
flips the physical DIR pin, so the carriage (Z scale) follows
jogSpeed × servoDir (× the physical wiring sign, +1 in the default
emulator config).

MEASUREMENT CAVEAT — serve-mode lash. EMU_SCENARIO overrides the config's
z_backlash_mm to 0.6 mm (reflex-fw emulator/src/main.cpp, "the measured
lathe value"; override via EMU_LASH_MM), so each PHYSICAL direction reversal
traverses up to 0.6 mm = 240 Z counts ≈ 151 servo steps of dead distance
before the carriage moves (verified: dead DISTANCE, not dead time — at 600
steps/s the dead band is the same 240 counts in half the time). Magnitude
assertions below leave room for one full lash traversal; signs are
unaffected (net motion always exceeds the lash).

Run (WSL only — the emulator Modbus link is a PTY):
  cd /mnt/c/projects/reflex/ui && uv run --frozen pytest -m system \
      tests/system/test_jog_mode.py -q
Add -s to see the observation tables from passing runs.
"""

import time
from fractions import Fraction

import pytest

from reflex.utils.ctype_calc import uint32_subtract_to_int32

pytestmark = pytest.mark.system

# Same envs the other stop/retract system tests use. EMU_NO_AUTO_RETRACT so a
# (never-expected) ELS stop event can't trigger the emulator's simulated
# hand-retract mid-observation.
_RUNNING = {"env": {"EMU_RPM": "30", "EMU_NO_AUTO_RETRACT": "1"}}
_STOPPED = {"env": {"EMU_RPM": "0", "EMU_NO_AUTO_RETRACT": "1"}}

# ── els_machine_mode.h mirror (wire contract: append, never renumber) ─────────
MODE_OFF, MODE_IDLE, MODE_FEEDING, MODE_MOVING = 0, 1, 2, 3
MODE_JOG, MODE_HELD, MODE_TAKEUP, MODE_CAL = 4, 5, 6, 7
MODE_NAMES = {0: "OFF", 1: "IDLE", 2: "FEEDING", 3: "MOVING",
              4: "JOG", 5: "HELD", 6: "TAKEUP", 7: "CAL"}

JOG_SPEED = 300.0        # steps/s — 300 × 0.00396875 mm/step ≈ 1.19 mm/s
COUNTS_PER_STEP = 400 * 0.00396875   # Z counts per servo step ≈ 1.5875


def _commission(h):
    """Real-machine commissioning, mirroring test_els_safety._commission:
    reverse=True / maxSpeed=10000 / accel=20000 (elspi ServoBar-0), reference
    geometry, 16 TPI feed (only relevant while the spindle turns)."""
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=True, max_speed=10000, acceleration=20000)
    h.commission_geometry()
    h.set_feed(Fraction(254, 160))


def _press_jog(h, speed):
    """EXACTLY JogBar.update_jog's press branch: jogSpeed first, then mode 2,
    through the Kivy properties (the production write path)."""
    h.board.servo.jogSpeed = speed
    h.board.servo.servoMode = 2


def _release_jog(h):
    """JogBar.update_jog's idle branch: jogSpeed=0, servoMode LEFT at 2."""
    h.board.servo.jogSpeed = 0


def _servo_mode_reg(h) -> int:
    """Direct Modbus read of fastData.servoMode (bypasses the poll cache)."""
    return int(h.register('fastData', 'servoMode'))


def _sync_enables(h):
    return [int(h.board.device['scales'][i]['syncEnable']) for i in range(4)]


def _derived_mode(h):
    """elsDeriveMachineMode (els_machine_mode.h) recomputed from live register
    reads, calRunning=0. Returns (mode, snapshot)."""
    snap = {
        'syncEnable': _sync_enables(h),
        'servoMode': _servo_mode_reg(h),
        'stepsToGo': int(h.register('servo', 'stepsToGo')),
        'enable': int(h.register('elsStop', 'enable')),
        'active': int(h.register('elsStop', 'active')),
        'takeupPending': int(h.register('elsStop', 'takeupPending')),
    }
    any_sync = any(snap['syncEnable'])
    if snap['takeupPending']:
        mode = MODE_TAKEUP
    elif snap['servoMode'] == 1 and snap['stepsToGo'] != 0:
        mode = MODE_MOVING
    elif snap['enable'] and snap['active']:
        mode = MODE_HELD
    elif snap['servoMode'] == 2:
        mode = MODE_JOG
    elif snap['servoMode'] == 0:
        mode = MODE_OFF
    elif any_sync:
        mode = MODE_FEEDING
    else:
        mode = MODE_IDLE
    return mode, snap


def _sample_window(h, seconds, derive_every=5):
    """Pump for `seconds`, recording per pump the servoMode value read off the
    wire this pump (board.update's fastData refresh) and Z counts; every
    `derive_every`th pump also the recomputed firmware-derived machine mode
    (~6 Hz, close to the real ledger's 10 Hz cadence).
    Returns (modes, derived, z): modes=[(t, servoMode)], derived=[(t, mode,
    snapshot)], z=[(t, z_counts)]."""
    t0 = time.monotonic()
    modes, derived, z = [], [], []
    i = 0
    while time.monotonic() - t0 < seconds:
        h.pump()
        t = round(time.monotonic() - t0, 3)
        modes.append((t, int(h.board.fast_data_values['servoMode'])))
        z.append((t, h.carriage_position_counts()))
        if i % derive_every == 0:
            m, snap = _derived_mode(h)
            derived.append((t, m, snap))
        i += 1
        time.sleep(0.02)
    return modes, derived, z


def _segments(modes):
    """Run-length encode a [(t, value)] timeline → [(value, t_first, t_last,
    n_samples)] for readable reports."""
    segs = []
    for t, m in modes:
        if segs and segs[-1][0] == m:
            segs[-1][2] = t
            segs[-1][3] += 1
        else:
            segs.append([m, t, t, 1])
    return [tuple(s) for s in segs]


# ═══════════════════════════════════════════════════════════════════════════
# FINDING 1a — the plain Jog screen flow, ELS disengaged.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "emulator_process", [_RUNNING, _STOPPED],
    ids=["spindle-running", "spindle-stopped"], indirect=True)
def test_jog_mode2_lands_and_persists(harness):
    """PINS the healthy contract: the JogBar property flow lands servoMode=2
    at the firmware, the register HOLDS 2 across ~2 s (>20 servoEnableTask
    ticks), jog release leaves it at 2, disable drops it to 0 and it STAYS 0
    (no firmware re-assert — the F5 sync-clear removed its enabling term),
    real motion occurred, and — with ELS disengaged — the firmware-derived
    machine mode during jog IS JOG(4), i.e. the ledger WOULD have published
    JOG. If this test passes, the real machine's missing-JOG is not
    reproducible through this UI+firmware path."""
    h = harness
    _commission(h)

    # Boot: firmware mode 0, nothing armed.
    assert _servo_mode_reg(h) == 0, "firmware should boot with servoMode=0"
    assert _sync_enables(h) == [0, 0, 0, 0]

    # Step 1 — the Jog screen's R-enable button (jogbar.kv on_release):
    # ServoDispatcher.toggle_enable → servoMode 1.
    h.board.servo.toggle_enable()
    enable_modes, enable_derived, _ = _sample_window(h, 0.3, derive_every=3)
    assert enable_modes[-1][1] == 1, (
        f"enable did not land servoMode=1: {_segments(enable_modes)}")
    # Production F5 behavior: mode 1 arms the spindle scale's syncEnable.
    assert _sync_enables(h)[0] == 1, (
        "spindle syncEnable should be armed while servoMode=1")

    # Step 2 — the jog press (JogBar.update_jog, dispatcher-level).
    pre_press = _servo_mode_reg(h)
    _press_jog(h, JOG_SPEED)
    post_press = _servo_mode_reg(h)      # direct register read, no pump yet
    assert post_press == 2, (
        f"servoMode=2 write did not land at the firmware "
        f"(register still {post_press} immediately after the property write)")

    z_jog_start = h.carriage_position_counts()
    jog_modes, jog_derived, jog_z = _sample_window(h, 2.0)
    z_jog_end = h.carriage_position_counts()

    print(f"\n[jog-press timeline] pre-press reg={pre_press}, "
          f"post-press reg={post_press}, then segments "
          f"{_segments(jog_modes)} (first samples: {jog_modes[:8]})")
    print(f"[derived modes during jog] "
          f"{[(t, MODE_NAMES[m]) for t, m, _ in jog_derived]}")

    # (b) It stays 2 — never rewritten across ~20 servoEnableTask periods.
    rewritten = [(t, m) for t, m in jog_modes if m != 2]
    assert not rewritten, (
        f"firmware servoMode left 2 during jog: {_segments(jog_modes)}")

    # The re-assert's enabling term is dead: mode-2 entry cleared every scale.
    assert _sync_enables(h) == [0, 0, 0, 0], (
        f"syncEnable not cleared during jog: {_sync_enables(h)} "
        "(the F5 clear-on-leaving-1 did not land)")

    # Real jog motion occurred (magnitude only — direction is FINDING 2's
    # question and is deliberately not asserted here). Gross ≈952 counts;
    # up to 240 may be consumed by the serve-mode 0.6 mm lash traversal
    # (see module docstring), so net ≈710-950.
    dz = z_jog_end - z_jog_start
    expected = JOG_SPEED * 2.0 * COUNTS_PER_STEP     # ≈ 952 counts gross
    assert 400 <= abs(dz) <= 1600, (
        f"jog produced {dz} Z counts, expected |dz| ≈ {expected:.0f} minus "
        f"up to 240 counts of lash")

    # Disengaged jog derives JOG(4): the ledger WOULD have published JOG.
    non_jog = [(t, MODE_NAMES[m]) for t, m, _ in jog_derived if m != MODE_JOG]
    assert not non_jog, (
        f"derived machine mode during disengaged jog was not JOG: {non_jog}")

    # Step 3 — release: JogBar leaves servoMode at 2 (only jogSpeed → 0).
    _release_jog(h)
    rel_modes, _, _ = _sample_window(h, 0.5, derive_every=10)
    assert all(m == 2 for _, m in rel_modes), (
        f"servoMode left 2 after jog release: {_segments(rel_modes)}")

    # Step 4 — disable (toggle_enable again): 0 lands and STAYS 0 for ~0.8 s
    # (~8 servoEnableTask ticks). A stale armed scale would re-assert 1 here.
    h.board.servo.toggle_enable()
    off_modes, _, off_z = _sample_window(h, 0.8, derive_every=10)
    assert all(m == 0 for _, m in off_modes), (
        f"servoMode did not stay 0 after disable (firmware re-assert?): "
        f"{_segments(off_modes)}")
    drift = abs(off_z[-1][1] - off_z[0][1])
    assert drift < 100, (
        f"carriage kept moving after disable: {drift} counts")


# ═══════════════════════════════════════════════════════════════════════════
# FINDING 1b — the hypothesis: ELS engaged-idle (enable=1, active=1) masks
# JOG in the derived mode, because HELD outranks it.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("emulator_process", [_RUNNING],
                         ids=["spindle-running"], indirect=True)
def test_engaged_hold_masks_jog(harness):
    """With ELS engaged (arm_idle_stop: elsStop.active=1 AND enable=1) the
    SAME jog flow moves the carriage with servoMode holding 2 — but the
    firmware-derived machine mode is HELD(5) for every sample, never JOG(4),
    because els_machine_mode.h ranks (enable && active) above servoMode==2.
    A day of jogging in this state writes zero JOG entries into the
    mode-watch ledger. This is the best emulator-reproducible explanation
    for the real machine's missing JOG."""
    h = harness
    _commission(h)

    z0 = h.z_scaled_position()
    h.set_stop_z(z0 - 50.0)      # mm; far stop, never reached by a 2.4 mm jog
    h.engage()
    assert h.els_fsm.state == "stopped"
    assert int(h.register('elsStop', 'enable')) == 1, "engage did not arm enable"
    assert int(h.register('elsStop', 'active')) == 1, (
        "engage did not arm the idle hold (arm_idle_stop sets active=1)")

    # Jog screen flow, unchanged: R-enable, then jog press.
    h.board.servo.toggle_enable()
    _sample_window(h, 0.3, derive_every=3)
    assert _servo_mode_reg(h) == 1

    _press_jog(h, JOG_SPEED)
    assert _servo_mode_reg(h) == 2

    z_start = h.carriage_position_counts()
    jog_modes, jog_derived, _ = _sample_window(h, 2.0)
    z_end = h.carriage_position_counts()

    print(f"\n[engaged jog] servoMode segments {_segments(jog_modes)}; "
          f"derived {[(t, MODE_NAMES[m]) for t, m, _ in jog_derived]}; "
          f"dz={z_end - z_start} counts")

    # The register itself is healthy: 2 lands and holds...
    assert all(m == 2 for _, m in jog_modes), (
        f"servoMode left 2 during engaged jog: {_segments(jog_modes)}")
    # ...and the jog physically moves the carriage (updateJogPosition ignores
    # the hold)...
    assert 400 <= abs(z_end - z_start) <= 1600, (
        f"engaged jog moved {z_end - z_start} counts, expected ≈710-950 "
        f"(952 gross minus up to 240 counts of lash)")
    # ...while the hold stays latched and the derived mode says HELD, never
    # JOG. THIS is how a real day of jogging publishes zero JOG(4).
    for t, m, snap in jog_derived:
        assert snap['enable'] == 1 and snap['active'] == 1, (
            f"hold unexpectedly released at t={t}: {snap}")
        assert m == MODE_HELD, (
            f"derived mode at t={t} was {MODE_NAMES[m]}, expected HELD "
            f"(snapshot {snap})")
    assert all(m != MODE_JOG for _, m, _ in jog_derived), (
        "derived mode published JOG despite the armed hold — "
        "els_machine_mode.h priority changed?")


# ═══════════════════════════════════════════════════════════════════════════
# FINDING 2 — jog direction vs commissioned servoDir. OBSERVATION TABLE ONLY.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("emulator_process", [_STOPPED],
                         ids=["spindle-stopped"], indirect=True)
def test_jog_direction_table(harness):
    """Measures the four (servo.reverse × jogSpeed sign) combinations with a
    stopped spindle (no sync contamination; mode 2 discards sync deltas
    anyway) and prints the signs of (a) the firmware's internal step counter
    (fastData.servoCurrent) and (b) the physical carriage (Z scale counts;
    emulator physical wiring signs are all +1 in lathe.toml).

    Direction is NOT asserted — the fix design depends on which of the two
    the Jog buttons should be anchored to. Assertions cover only: motion
    happened, magnitudes are consistent (allowing one 240-count serve-mode
    lash traversal per physical reversal, see module docstring), and
    servoMode stayed 2 throughout.
    """
    h = harness
    h.configure(is_threading=False, retract_enabled=False,
                wizard_enabled=False, els_forward=True)
    h.commission_servo(reverse=False, max_speed=10000, acceleration=20000)
    h.commission_geometry()

    h.board.servo.toggle_enable()      # R-enable, as on the Jog screen
    _sample_window(h, 0.2, derive_every=10)
    assert _servo_mode_reg(h) == 1

    rows = []
    for reverse, sign in [(False, +1), (False, -1), (True, +1), (True, -1)]:
        h.set_servo_reverse(reverse)   # production path → servoDir register
        h.pump()
        servo_dir = int(h.register('servo', 'servoDir'))
        assert servo_dir == (-1 if reverse else 1), (
            f"servoDir register {servo_dir} does not reflect reverse={reverse}")

        h.pump()
        z_start = h.carriage_position_counts()
        s_start = int(h.board.fast_data_values['servoCurrent'])

        _press_jog(h, sign * JOG_SPEED)
        jog_modes, _, _ = _sample_window(h, 1.0, derive_every=50)
        _release_jog(h)
        _sample_window(h, 0.4, derive_every=50)   # decelerate + settle

        z_end = h.carriage_position_counts()
        s_end = int(h.board.fast_data_values['servoCurrent'])
        ds = uint32_subtract_to_int32(s_end, s_start)
        dz = z_end - z_start

        assert all(m == 2 for _, m in jog_modes), (
            f"servoMode left 2 mid-combo (reverse={reverse}, sign={sign:+d}): "
            f"{_segments(jog_modes)}")
        rows.append((reverse, servo_dir, sign, ds, dz))

    # Print the whole table BEFORE any magnitude assertion, so one odd row
    # still leaves the full observation set in the report.
    print("\n[jog direction table]  (emulator physical wiring all +1; "
          "Δservo = fastData.servoCurrent internal step counter; "
          "Δz = Z scale counts = physical carriage)")
    print(f"{'reverse':>8} {'servoDir':>9} {'jogSpeed':>9} "
          f"{'Δservo':>8} {'Δz':>8}  relation")
    for reverse, servo_dir, sign, ds, dz in rows:
        rel = (f"sign(Δservo)={'+' if ds > 0 else '-'}=jogSpeed; "
               f"sign(Δz)={'+' if dz > 0 else '-'}="
               f"{'jogSpeed×servoDir' if (dz > 0) == (sign * servo_dir > 0) else 'UNEXPECTED'}")
        print(f"{str(reverse):>8} {servo_dir:>+9d} {sign * int(JOG_SPEED):>+9d} "
              f"{ds:>+8d} {dz:>+8d}  {rel}")

    # Magnitude pins only (direction stays observation-only). ≈300 steps
    # commanded per combo → ≈476 gross Z counts at 1.5875 counts/step, of
    # which up to 240 counts can be consumed by the serve-mode 0.6 mm lash
    # when the leg physically reverses the previous one.
    LASH_COUNTS = 240
    for reverse, servo_dir, sign, ds, dz in rows:
        assert 150 <= abs(ds) <= 600, (
            f"unexpected servo step count (reverse={reverse}, sign={sign:+d}): "
            f"ds={ds}")
        gross = abs(ds) * COUNTS_PER_STEP
        loss = gross - abs(dz)
        assert -30 <= loss <= LASH_COUNTS + 40, (
            f"carriage motion inconsistent with commanded steps minus at most "
            f"one lash traversal (reverse={reverse}, sign={sign:+d}, ds={ds}, "
            f"dz={dz}, gross≈{gross:.0f}, loss≈{loss:.0f})")
        assert abs(dz) >= 150, (
            f"carriage barely moved (reverse={reverse}, sign={sign:+d}): dz={dz}")
