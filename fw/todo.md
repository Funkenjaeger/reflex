# reflex-fw — TODO / follow-ups

Tracking file for deferred work, known workarounds, and follow-ups. Add items here
instead of leaving bare `TODO:` comments in code.

---

## Repo admin

### Switch default branch back to `main` once the first release is pushed
- **Why:** `hard-fork` (the old default + fork-import branch) has been retired. The
  default branch is temporarily set to **`dev`** so day-to-day work has a sensible
  default without touching `main` (a push to `main` triggers the release pipeline,
  and we want the first release to be solid first).
- **Do when:** the first real release is ready to ship from `main`.
- **How:** `gh repo edit --default-branch main`, then `git remote set-head origin main`.
- Lineage at the time of the switch: `main` ──(+8)──► (old `hard-fork`) ──► `dev`
  (dev contains everything). Decide whether to advance `main` first.

## Modbus communication

### Modbus RX DMA — shelved, needs on-hardware debugging
- **Status:** the working fix is the **interrupt-mode overrun recovery** on branch
  `fix/modbus-uart-overrun-recovery` (commit `dc86a29`). It is validated: zero CRC
  errors through a real ELS *cutting* pass on the Pi. **This is the one to ship/merge.**
- **Root cause (for context):** Modbus RX ran byte-at-a-time in interrupt mode at the
  lowest NVIC priority (USART1 = 15) and was starved by the 100 kHz step-generation
  ISR (TIM9 → `SynchroRefreshTimerIsr`, NVIC prio 5). Starvation → USART overrun (ORE)
  → dropped bytes → CRC errors + connection flapping. The stopgap recovers from each
  overrun instead of leaving RX stalled until reconnect.
- **DMA attempt (does NOT work):** branch `fix/modbus-rx-dma` (commit `9cb4381`) wires
  USART1_RX to DMA2 Stream2 Ch4 and sets `xTypeHW = USART_HW_DMA`. On the bench the UI
  never connected (slave never responds), so RX DMA isn't delivering frames. Cause not
  found by code inspection — needs a logic analyzer on the RS-485 line and/or an SWD
  debugger to localize. Top suspects, in order:
  1. First-frame IDLE handling — `HAL_UARTEx_ReceiveToIdle_DMA` may need an explicit
     `__HAL_UART_CLEAR_IDLEFLAG` before arming, or IDLE-interrupt enable not taking;
     if the first frame never completes, the slave never responds.
  2. NORMAL-mode re-arm race in `HAL_UARTEx_RxEventCallback` between frame completion
     and re-arming the next receive.
  3. DMA2 Stream2/Ch4 mapping — correct per RM0383 for USART1_RX, but confirm on the
     scope that DMA activity actually occurs.
- **Decision:** DMA is an optional future optimization (prevents overruns vs. recovering
  from them). Only worth pursuing if much higher spindle RPM / step rates during
  threading start producing errors the stopgap can't keep up with — and only with a
  debugger attached, not blind.

### Optional: move Modbus TX to DMA
- TX still uses `HAL_UART_Transmit_IT` (`sendTxBuffer`, Modbus.c). The master tolerates
  inter-byte gaps within its read timeout, so this is fine today. If heavy-load TX-side
  corruption ever appears, move TX to DMA (USART1_TX = DMA2 Stream7 Ch4).

### Implement Modbus function `MB_FC_WRITE_MULTIPLE_COILS`
- `Core/Src/Modbus.c:884` — case is stubbed (`// TODO: implement "sending coils"`).
  Not currently needed by the UI; implement if/when coil writes are used.

### Optional: implement Modbus diagnostic accessors
- `Core/Inc/Modbus.h:271` — a block of original-library prototypes is left
  unimplemented (`getInCnt`/`getOutCnt`/`getErrCnt`/`getID`/`getState`/`getLastError`/
  `setID`/`setTxendPinOverTime`/`ModbusEnd`). The handler already tracks
  `u16InCnt`/`u16OutCnt`/`u16errCnt`, so exposing counters for diagnostics would be cheap
  if we ever want comms health metrics.

---

## Real-time / performance

### Reduce 100 kHz synchro-ISR CPU load
- `SynchroRefreshTimerIsr` (`Core/Src/Ramps.c`) runs every 10 µs (TIM9, 100 kHz) at
  NVIC prio 5 and does non-trivial work every tick (phase correction, elsStop
  bookkeeping, GPIO). This is what starved Modbus RX in the first place. Moving
  non-time-critical work (elsStop state bookkeeping, phase-correction math) out of the
  ISR into a normal FreeRTOS task would free CPU, cut the worst-case ISR duration, and
  reduce starvation pressure on everything else. Keep only the must-happen-now step
  pulse in the ISR.

---

## ELS shoulder stop

### Resume is silently skipped when the carriage hasn't retracted (KNOWN FAILING TEST)
- **Reproduction:** `emulator/test/els_stop_resume_relatch_test.cpp`, CTest target
  `els_stop_resume_relatch_test`. It is RED on purpose; 2 assertions fail. The
  same file contains passing controls, so a green run would mean the bug is fixed.
- If SW resumes by writing `elsStop.active = 0` while the reference axis is still
  at/past `elsStop.stopPosition`, the trigger test at `Core/Src/Ramps.c:403` (inside
  the per-scale loop, so EARLIER in the pass) re-latches `active = 1` before the
  1→0 edge test at `Core/Src/Ramps.c:455` runs. The edge is consumed within the
  pass, so the backlash takeup and `applyPhaseCorrection` are both silently
  skipped — `takeupPending` never sets and `stepsToGo` never moves. The machine
  just looks like it re-stopped.
- **FIXED 2026-08-07 in `aa07cff`.** `elsStop.hysteresis` (`Core/Inc/Ramps.h:102`)
  was declared and never read anywhere in the firmware, while reflex-ui actively
  wrote it — so the 800 it wrote for standalone-stop was a no-op and every stop
  behaved as tight/0. It is now read by the trigger gate.
  Both open questions were decided: retract is measured **from `stopPosition`**,
  and `hysteresis == 0` is an explicit **no-op gate** preserving prior behavior,
  which is what keeps reflex-ui's `0` writes for guided/retract/wizard modes safe.
  Implementation is one tracking field in `rampsHandler_t` plus ~9 lines inside the
  `Ramps.c:403-408` block; `Ramps.c:455` is untouched.
  Emulator ctest went 3/4 → 4/4: the two `a165e07` repro assertions now pass
  **unmodified**, and the gate was mutation-tested in both directions (forcing the
  cleared flag true fails all five new GATE assertions; setting `hysteresis = 0`
  restores the pre-fix outcome exactly).
  Consequence for the ELS auto-start plan: `hysteresis` is now live, so
  `armRetractCounts` must take a **fresh** register rather than reusing this one.
- **NOT proven on hardware.** The emulator has no servo dynamics, no Modbus timing
  and no metal. Do not treat 4/4 as a machine result.

---

## ELS backlash: closed-loop calibration + take-up confirmation (2026-08-08)

### Landed
- The take-up is no longer open loop. Completion now requires **confirmed Z-scale
  motion**, not just "the firmware finished issuing the pulses it decided to
  issue". Fails closed: no motion means `takeupPending` stays set, sync stays
  gated, and `applyPhaseCorrection()` does not run on an uncoupled drivetrain.
  Recovery is the `elsStop.enable` 1->0 escape hatch.
- **(2026-08-21) The take-up and its confirmation run on EVERY pass -- the first
  pass of a job and turning included.** Until then both hid behind the
  phase-correction condition (`referenceLatched && pitch != 0`), so the datum
  pass and every turning pass were the only ungated passes in the system. The
  two jobs on the resume edge are now gated separately: take-up needs only a
  configured backlash; `applyPhaseCorrection()` additionally needs a latched
  reference and pitch, carried to the confirmation's success branch by
  `elsStopCorrectOnConfirm`. The take-up direction in turning comes from the
  SIGN of `zCountsPerPitch`, which reflex-ui now writes signed with pitch = 0
  (`push_turning_geometry`). Pins: `els_takeup_confirm_test` first-pass /
  turning / polarity scenarios. Decided by Evan 2026-08-21 (task 6a81fa2f).
- `calCommand`-driven calibration measures the lash directly (three reversals,
  counting servo steps until Z moves). Host judges consistency and writes
  `backlashSteps = measured + max(20%, floor)`.
- `protocolVersion` register added and checked by reflex-ui at connect.
- `rampsSharedData_t` 264 -> 300 bytes; `KNOWN_ROOT_SIZE` moved in lockstep.
- Superseded local commit `d917641` (a takeup gate built around an operator-set
  `takeupMinZCounts` threshold rather than a measurement). Rewound; preserved on
  branch `archive/d917641-takeup-zconfirm` — its 470-line test is worth mining.

### Two defects found by the ISR-level tests, both real
- **Calibration must gate sync.** `scales[].syncEnable` is independent of
  `elsStop.enable`, so a turning spindle drives the leadscrew straight through
  the measurement. Guarding on `enable` alone was insufficient.
- **Legs must arm on the first pulse in the NEW direction**, not when the
  reversal is commanded. The ramp overshoots while decelerating and that return
  travel is lash traversal; baselining at the command instant measured the
  deceleration transient instead (6 steps against a true lash of 60).

### NOT proven on hardware
- Emulator + host tests only: no servo dynamics, no Modbus timing, no metal.
- The calibration run is the first thing in this codebase that commands
  **bidirectional carriage motion**. Bench it with the tool well clear before it
  ever sees a workpiece.
- `calCeilingSteps` and `calMotionThreshCounts` are machine-specific and have
  never been set on elspi. Defaults are sized for 200 counts/mm Z; the emulator
  is 400 counts/mm.
- Open question for the machine: the measurement reads HIGH by the detection
  distance (~2 Z counts ~= 5 servo steps on elspi). That bias is conservative
  and deliberate, but confirm the real magnitude on metal before tuning the
  margin floor down.

### The gate's dwell and the attribution horizon disagree by 20x (2026-08-22)
- `ELS_SETTLE_TICKS` (50) is how long the gate waits after the take-up's last pulse
  before deciding; `ELS_SLIP_SETTLE_TICKS` (1000) is how long motion after a pulse is
  still credited to the servo. Same code path, same physical settle, 20x apart.
- Nobody has measured which is right, and until 2026-08-22 nobody could: the
  takeup-settle probe was structurally unable to watch a confirmed take-up (see DIAG.md).
  `takeup-settle-v3` holds the gate open for its window and can.
- **If the settle is long,** the gate releases the cut while the carriage is still
  moving. **If it is short,** the attribution horizon is 20x too generous and the
  hand-nudge window is far wider than it needs to be. Both are worth knowing.
- Blocked on: one v3 capture session on a coupled take-up.

### Commission `ELS_SLIP_SETTLE_TICKS` on elspi (UNMEASURED PARAMETER)

Motion attribution (`Core/Inc/els_slip.h`) replaced the 250 ms confirmation
window as the thing that actually bounds the 2026-08-08 exposure. The number
that bound is now made of — `ELS_SLIP_SETTLE_TICKS` in `Ramps.c`, currently
**1000 ticks (~10 ms at the 100 kHz ISR rate)** — has never been measured on the
machine, and **cannot be measured in the emulator**: its lash model moves the
carriage instantaneously with the pulse, so it has no settle behaviour at all.
The value there satisfies the structural constraints (above `ELS_SETTLE_TICKS`,
above pulse pacing) and nothing more.

To commission it: run a real take-up at the take-up speed actually in use and
watch how long Z counts keep arriving after the last commanded pulse. Set the
horizon just above that. Tune it **down** from 1000 against a machine that still
confirms reliably — never up to make a refusal go away, since the horizon is
exactly the interval in which a hand nudge is still accepted as evidence.

**2026-08-18 status — this entry was nearly closed on bad data; it stays
open.** The 2026-08-16 takeup-settle-v2 captures (13 rows, all-zero) were for
a time read as answering this. An audit
(`els-settle-measurement-findings-2026-08-18.md`; DIAG.md's schema-2 section
carries the summary) showed they cannot: the capture window was 500 ticks —
half this constant — the era's recorder discarded `end_reason` so truncated
captures are indistinguishable from finished ones, and the armed window never
demonstrated it could see motion at all. All three are now addressed in code
or procedure: the window is 2000 ticks (`ELS_DIAG_BUCKET_TICKS` 40), the
recorder exports `end_reason`/`capture_ticks`, and the next capture session
MUST (a) include one condition known to move Z during the armed window —
prove a nonzero before trusting a zero — and (b) read `settle_ticks` only
from `END_PULSE` captures; an `END_WINDOW` capture is a lower bound.

Two unit traps, both live (full list in `els_slip.h`):
- **Ticks, not milliseconds.** The emulator's real-time serve loop drives the
  same ISR ~10x slower than hardware (`emulator/src/main.cpp`), so a horizon
  chosen by wall-clock there is 10x wrong on the lathe.
- **200 vs 400 counts/mm.** The horizon itself is a time quantity and so is
  resolution-independent, but any *counts* threshold eyeballed off emulator
  output is 2x wrong on elspi.

---

## protocolVersion history (RESOLVED 2026-08-22)

- **3**: `machineMode` promoted to a permanent register, so the rung-2 census
  collects in every build rather than only under a mode-watch probe.
- **4**: the manual reference latch merged and appended `latchCommand`/`latchSeq`.
  That branch had also written 3; the scratch-test pin is what forced the
  renumber instead of two layouts sharing a number. Next append takes **5**.
- The house rule that made this cheap: reflex-ui checks `protocolVersion` at
  connect, so a mismatch names itself instead of surfacing as plausible garbage.

## ELS thread-phase offset (2026-08-21)

### Landed: the primitive
- `elsComputePhaseCorrection()` takes `offsetSteps` (leadscrew steps), summed into
  `phaseError` before the mod-pitch fold and the forward-bias; `els_phase_offset_test`
  pins the boundaries (T1 exact regression at 0, T3 one pitch = no-op, T5 negative
  forward-biases to pitch-|offset|). The only call site passes 0.
- **Decided (Evan, 2026-08-21): cumulative entry, running total shown.**

### Landed: the register/command half (2026-08-22)
- `phaseOffsetCommand` / `phaseOffsetSeq` / `phaseOffsetPending` / `phaseOffsetSteps`
  appended to `elsStop_t`, `protocolVersion` -> **5**, consumed in the ISR with the
  `calCommand` idiom, cleared on the enable 0->1 edge alongside `referenceLatched`,
  and fed to `elsComputePhaseCorrection()` in place of the placeholder 0.
- `els_phase_offset_command_test` pins the plumbing: accept / refuse-outside-a-job /
  Pending-inert-without-Command / replace-not-accumulate / dies-with-the-job-survives-
  the-pass / reaches-the-math / polarity. Eight mutations applied and killed; M7
  (call site still passing 0) leaves 23 of 26 assertions green, which is why the
  reaches-the-math case exists.
- Host half in `els_fsm.py`: conversion, accumulation, and all refusals, with
  `tests/fsms/test_els_phase_offset.py` (25 cases, nine mutations killed).

### Decided 2026-08-22 (Evan)
- Running total displayed in the **advanced bar** when nonzero, as distance AND
  fraction of pitch.
- **Advance-only** entry plus a Clear. A signed control would misrepresent the math:
  a negative offset jogs forward by pitch-|offset|, never back by |offset|.
- **Refuse** at one pitch with a stated reason, never clamp -- a clamp hands back a
  different thread start than the one asked for, in metal, before anything looks wrong.

### Remaining
- **THE POLARITY QUESTION, for the bench.** The offset is summed into `phaseError`
  raw, so it displaces phase in the MACHINE frame; on a `cuttingDir == -1` machine a
  given entry selects the COMPLEMENTARY start of an N-start thread (2/3 pitch where
  the operator pictured 1/3). The lathe's own fixture geometry says elspi is
  `cuttingDir == -1`. Not a safety issue -- every start is a legitimate start, and
  cumulative entry self-corrects -- but it decides whether the UI's wording is honest.
  Settle it by cutting a 2-start (where it is symmetric and therefore safe) and then a
  3-start. `els_phase_offset_command_test` case 7 prints the corrections for both
  polarities to compare against.
- Hardware verification of the whole feature: nothing here has been near a lathe.
- Second source: 6a77c598 (X-depth compound infeed) feeds the same `Pending` path from
  `scaledPosition` * tan(theta); do not build a second offset path.

## Machine mode: the call site nobody can test (2026-08-22)

`elsPublishMachineMode()` is unit-tested (`els_machine_mode_test`) and its
mutation is caught. What is NOT covered by any automated test is whether the
CALL SITE in `servoEnableTask` actually runs: the emulator does not execute the
FreeRTOS tasks at all -- it reimplements the parts it needs in
`emulator/src/main.cpp`, which is why that file now mirrors this publication on
its own ~100 ms divider. Removing the firmware call site breaks nothing in CI.

Declared rather than papered over. It is verified by reading
`elsStop.machineMode` off the real machine after a flash, and any task-side
firmware logic added later has the same gap.
## ELS interactive re-sync: manual reference latch (2026-08-08)

### Landed (branch feat/els-thread-resync)
- `latchCommand`/`latchSeq` appended to `elsStop_t` (96 -> 100 bytes,
  `rampsSharedData_t` 304 -> 308, `protocolVersion` 1 -> 2, reflex-ui
  `devices.py` + `KNOWN_ROOT_SIZE` moved in lockstep). Same command/ack
  contract as `calCommand`/`calSeq`; a latch with `enable == 0` is consumed
  with NO seq increment (absent ack = refusal).
- ISR consumes the command in one pass: captures `latchedSpindle`/`latchedZ`
  coherently, sets `referenceLatched` (which is what suppresses the
  first-trigger auto-latch), acks via `latchSeq`. Mechanism only — fresh-job
  policy lives in the reflex-ui wizard.
- `els_manual_latch_test` (ISR-level, mutation-proven ×4) and reflex-ui's
  `tests/system/test_els_thread_resync.py` (emulator end-to-end: 3 passes,
  mid-cut phase residual spread 1.4 steps on a 400-step pitch).

### NOT proven on hardware
- Emulator + host tests only: no servo dynamics, no Modbus timing, no metal.
- The elspi verification is a real re-chucked threaded part (TickTick task
  6a768a98 checklist item 8): jog into the thread, hand-seat, latch, AIR PASS
  first, then confirm passes chase the existing groove.
- The 1–3 count Z-hold tolerance and the spindle stillness dwell (~0.7 s,
  ±1 count) have never been exercised against real scale jitter — elspi's Z
  is 200 counts/mm, half the emulator's resolution.

---

## Emulator

### Model manual carriage movement with the half-nut open (TEST-INFRASTRUCTURE GAP)

**Found by hardware, 2026-08-08. This gap is why a real defect in the take-up
confirmation gate was unreachable by any test.**

The emulator (and `els_takeup_confirm_test.cpp`'s `Lash` model) computes the
carriage position as a PURE FUNCTION of `servo.currentSteps`. There are exactly
two worlds: coupled, where Z moves when driven past lash, and uncoupled, where
Z NEVER moves. Reality has a third — **uncoupled but moving anyway**, because
the operator pushed the carriage with the half-nut open.

That third state is not a corner case here. Manual carriage movement with the
nut open is a first-class part of how this machine is used: the whole ELS stop
resume model is built on the operator hand-cranking between passes, and the
interactive re-sync feature (task 6a768a98) is entirely about it.

**What it cost:** the take-up confirmation gate tested "did Z move?" when it
needed to test "did Z move BECAUSE WE DROVE IT?". On hardware, a withheld
take-up was satisfied by the operator nudging the carriage by hand with the nut
open. No test could express that, because the model has no input for carriage
motion that the servo did not cause.

**Needed:**
- ~~The same degree of freedom in the unit-test `Lash` fixture~~ — DONE.
  `Rig::nudgeCarriage()` injects carriage motion the servo did not cause,
  independently of the serve-mode command channel.

  **DECIDED 2026-08-13 (Evan): keep `Rig::nudgeCarriage()` and `LathePhysics`
  separate. Do NOT promote it into the shared model.** This item previously
  recorded the *fact* of the split with no rationale, which is why it kept
  reading as unfinished. The rationale:

  `LathePhysics` already has a carriage-displacement input — `z move` / `z jog`
  via the serve-mode channel, added 2026-08-10 (see the entry below). Crucially
  `moveCarriageTo`/`jogCarriage` **refuse while the nut is ENGAGED**, and that
  refusal is deliberately part of the physics model: "only valid with the nut
  open" is a property of the machine, so it belongs where the machine is
  modelled.

  `Rig::nudgeCarriage()` is a bare `zBase += zCounts` on the ISR-level `Rig`
  with no such guard, and that is correct for what it is. An ISR-level harness
  must be able to inject states the physics model forbids — that is the whole
  point of testing what firmware does when the world misbehaves.

  So promotion has only two outcomes and both are bad: the promoted function
  keeps its lack of a guard, and the physics model acquires a hole that lets
  production paths move the carriage through an engaged nut; or it acquires the
  guard, and the ISR tests can no longer inject the uncoupled-but-moving case
  they exist to cover. They are not duplicates — they model different layers,
  and the missing piece was only ever this paragraph.

  Detail: journal 2026-08-13. **This decision is estate-level, not branch-level
  — the wiki/journal copy is authoritative; this note is a mirror that only
  reaches branches as they merge.**
- ~~Regression case: a withheld take-up must NOT be satisfied by hand motion~~ —
  DONE, and it is what pins motion attribution: `els_takeup_confirm_test.cpp`
  now runs an identical 20-count shove twice inside the same open window, once
  ~50 ticks after the last pulse (confirms — inertia) and once 5000 ticks after
  (refused — a handwheel). Mutation-proven selective: disabling attribution
  reddens the second and leaves the first green.
- ~~Regression case: a calibration leg must not be satisfied by hand motion
  either~~ — DONE 2026-08-13, `emulator/test/els_cal_nudge_refusal_test.cpp`.

  **This bullet used to say the defect was STILL OPEN, and that was stale.** It
  described `elsCalUpdate()` as still using the bare `elsZMotionSeen()` endpoint
  test with the accumulator fed only while `takeupPending`. Commit `84c396b`
  (2026-08-10) had already landed the fix on this branch: `elsCalTick()` decides
  on `elsSlipConfirmed(&ctx->slip, ...)`, `elsCalCtx_t` carries its own
  `elsSlipAccum_t`, and `Ramps.c` ticks it from the same point in the ISR the
  take-up's accumulator is ticked from. What was actually missing was any test
  that would notice if that came back out.

  It is now the take-up file's paired-shove convention one layer down: the same
  20-count shove is delivered to the same armed MEASURE leg of the same coupled
  rig, once with the servo mid-drive (confirms — indistinguishable from inertial
  settle, and a healthy slow machine depends on that) and once after the servo
  has been silent past the settle horizon (refused). Mutation-proven: reverting
  the `moved` line to `elsZMotionSeen()` makes the refused arm record a bogus
  2-step "lash" and re-baseline `stepsRef`, and left the verdict of all eight
  pre-existing targets intact.

  **What still is NOT covered, and cannot be yet:** the `&& data->elsCal.armed`
  gate on `Ramps.c`'s calibration attribution-tick block. Deleting that clause
  leaves all nine targets green, because `elsCalTick()` calls `elsSlipReset()`
  at the instant a leg arms and nothing reads the accumulator before that — the
  gate is defense in depth, not observable behavior. Writing a test for it needs
  a consumer of the pre-arm accumulator to exist first.
- STILL OPEN: **the mid-drive shove remains undecidable, and that is a sensor
  problem, not a code problem.** A nudge landing inside the settle horizon of a
  real pulse cannot be told from genuine inertial settle using Z and commanded
  steps alone (`els_slip.h`, "WHAT THIS DOES NOT DO"), and on a calibration leg
  the leadscrew is turning for the whole leg, so that is the entire exposure
  window. `els_cal_isr_attribution_test.cpp` and the KNOWN GAP case in
  `els_backlash_cal_test.cpp` both assert it is open so it cannot quietly be
  mistaken for closed. Closing it needs a detector of a different shape (e.g.
  correlating Z RATE against commanded step rate over a sliding window) — design
  work, and nothing goes near elspi without sign-off.
- ~~An external carriage-displacement input to `LathePhysics`~~ — DONE
  2026-08-10. The serve-mode stdin channel (`emulator/src/main.cpp`) gained
  `z move <mm>` / `z jog <-1|0|1>` and `halfnut open` / `halfnut close`.
  Together they express the third state: **uncoupled but moving anyway**.
  `moveCarriageTo`/`jogCarriage` already refuse while the nut is ENGAGED, so
  the "only valid with the nut open" rule stays inside the physics model.

  `halfnut` takes an explicit STATE, not a toggle, and the distinction is
  load-bearing: a toggle issued while an engage is waiting for phase alignment
  CANCELS it, so a toggle-shaped command means different things depending on
  state a test cannot see. `LathePhysics::setHalfNutEngaged()` is idempotent;
  `els_halfnut_test` T8 pins all four transitions, and the naive
  "toggle if it disagrees" implementation reddens 6 of its assertions.

  Serve mode still force-engages the nut at boot for **any** `EMU_SCENARIO`
  value — deliberately left alone. A test that wants the nut open now says so,
  which keeps every existing scenario working unchanged.

- ~~SYSTEM-level regression~~ — DONE 2026-08-10, and it lives in the OTHER
  repo: `reflex-ui/tests/system/test_els_takeup_attribution.py`. Runs the real
  operator cycle (cut a pass → stop → retract → open the nut → press Cut) and
  shoves the carriage mid-window. Mutation-proven: reverting the firmware gate
  to the endpoint comparison reddens the refusal case while the coupled
  positive control stays green.

  Note for whoever moves this next: the take-up does NOT run on the first cut.
  The resume path needs `referenceLatched`, which the FIRMWARE sets at a real
  stop trigger — so a take-up always has a completed pass in front of it. That
  is also precisely the situation the 2026-08-08 defect occurred in.

**Design note for whoever does this:** a fixture that cannot express a failure
makes tests that agree with the code and are wrong together. The take-up gate
assumed coupling in order to test FOR coupling, and the fixture inherited the
same assumption, so both were self-consistent and both were wrong.


### Dashboard: show the real backlash offset
- `emulator/src/dashboard.cpp` Z-axis status line prints a hardcoded
  `backlash: 0.0` (`toDisplay(0.0)`) instead of the physics model's actual nut
  position. Wire it to `physics.getBacklashOffsetMM()` so lash-wall state is
  visible when eyeballing half-nut engagement / takeup behavior.

---

## Hardware workarounds (remove when HW is fixed)

### STEP duplicated on SPARE_2 (PA3) — PCB wiring workaround
- The current PCB has a wiring issue where the primary STEP pin isn't driven, so the
  firmware mirrors every STEP pulse onto SPARE_2 (PA3) as the actual step output. See
  `Core/Src/Ramps.c` `SynchroRefreshTimerIsr` (the paired `STEP_PIN` + `SPARE_2_PIN`
  writes) and pin defs in `Core/Inc/Ramps.h`.
- **Remove the SPARE_2 mirroring once the next HW revision drives STEP correctly**, and
  reclaim PA3 as a real spare.
