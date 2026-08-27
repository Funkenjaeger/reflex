# Diagnostic probes

Temporary firmware instrumentation, and the rules that keep it from leaking into
a build that runs a lathe.

A **probe** is a block of measurement code compiled in only on request. It writes
to the 64-register *diagnostic scratchpad* reserved at the tail of `elsStop_t` —
reserved in **every** build, so its offset never moves, but written only in a
diagnostic build. In a release build the whole block reads zero.

```bash
./scripts/build.sh --diag=takeup-settle-v2     # build with one probe
./scripts/flash.sh --diag=takeup-settle-v2     # build it and flash it
./scripts/build.sh --diag                      # lists what is available
```

Never on `dev-staging`, `dev` or `main`.

## One probe at a time

Every probe writes the same 64 registers. Two compiled in together would
interleave their fields and produce a capture that looks well-formed and means
nothing.

This is enforced by shape, not by rule. Selection is a single macro holding a
single value — `-DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_<NAME>` — so "two probes at
once" is not a state the build system can express. There is no flag combination
that produces it, and nothing to remember.

The surrounding guards in `Core/Inc/Ramps.h` reject the near misses, all at
compile time:

| You pass | Result |
|---|---|
| nothing | release build, scratchpad reads zero |
| a registered probe | that probe, and only that probe |
| `ELS_DIAG_SCRATCH` by hand | **compile error** — it is derived, never passed |
| a misspelled macro name | **compile error** — would otherwise expand to `0` and silently build a "diagnostic" image carrying no probe |
| a retired probe | **compile error**, naming its replacement |
| an unregistered id | **compile error** |

The misspelling case is the one worth understanding. An undefined identifier
evaluates to `0` in the preprocessor, so `-DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_TAKUP_SETTLE_V2`
would quietly mean "no probe" while the build still called itself diagnostic —
you would flash it, capture nothing, and have no error anywhere to explain why.
Rejecting `NONE` explicitly is what turns that into a build failure.

## Knowing what is running

`elsStop.diagSchema` names the probe compiled in; `0` means none. **A reader must
check it and refuse any id it does not recognise.** `protocolVersion`
deliberately does *not* move when a probe changes — the whole point of reserving
the block is that adding a probe does not change the layout — so `diagSchema` is
the only thing standing between a reader and a plausible number with the wrong
meaning.

reflex-ui mirrors these ids and logs the schema at connect. `scripts/flash.sh`
records the probe in `~/firmware/flashed.json` alongside the git revision, so a
capture pulled later can be traced to what was measuring it.

Schema ids are a wire contract: **append only, never renumber.** A retired id is
never reissued to a different probe — a stale reader that still recognises an old
id must not silently accept new data under it.

## Code layout — the pattern

Probes follow a fixed shape so that adding one is mechanical and `Ramps.c` never
learns which probe it is calling.

| File | Role |
|---|---|
| `Core/Inc/els_diag.h` | dispatch. Includes the selected probe's header, or supplies no-op entry points. **Always** included, from the foot of `Ramps.h`. |
| `Core/Inc/els_diag_<name>.h` | one probe. Its state machine, its constants, its trace geometry. |
| `Core/Inc/Ramps.h` | the scratchpad registers and schema ids (register contract), plus `elsDiagCtx_t` |
| `Core/Src/Ramps.c` | **call sites only** — no probe logic, and no `#ifdef` |

Naming: files `els_diag_*.h`, functions `elsDiag*`, matching the existing
`els_slip.h` / `els_backlash_cal.h` modules this pattern is modelled on.

**Every probe implements the same five entry points.** That fixed contract is
what keeps `Ramps.c` probe-agnostic:

| Entry point | Called at |
|---|---|
| `elsDiagInit(ctx, stop)` | `RampsStart`. Publish `diagSchema` + geometry, clear the block |
| `elsDiagArm(ctx, stop)` | take-up initiation, before any pulses |
| `elsDiagCaptureStart(ctx)` | first tick at which commanded motion is complete |
| `elsDiagCapturing(ctx)` | cheap predicate — see below |
| `elsDiagTick(ctx, stop, dZ, dServo)` | once per ISR tick while capturing |
| `elsDiagServoGate(ctx, stop, mode)` | `servoEnableTask`, at the re-assert decision; `true` suppresses it (schema 3+) |
| `elsDiagTaskTick(ctx, shared, cal)` | once per `servoEnableTask` iteration (~100 ms), after the re-assert (schema 4+) |

**`elsDiagCapturing` must stay trivial, and the ISR must call it before
computing `elsDiagTick`'s arguments.** C evaluates arguments before the callee
can early-return, so an unguarded call makes every tick pay for `dZ` (two array
indexings and a multiply) and `dServo` (a subtract) whether or not a capture is
running. That cost lands in the ISR a timing probe exists to measure, which
makes it self-defeating rather than merely wasteful. Measured: **+128 bytes of
ISR** when the guard was missing, versus 20 bytes *smaller* than the pre-refactor
baseline with it.

**Call-site placement is not the probe's to choose.** The bodies live in the
probe header; the instants do not. Each hook sits at one specific point in the
tick — `elsDiagArm` clears at initiation precisely because the capture-start tick
is the one being measured — and a relocated hook measures something else. The
entry-point comments carry that constraint, because it is no longer visible from
the code surrounding the call.

**Release cost is no code.** The no-op entry points, `elsDiagCapturing` returning
a constant `false`, let the whole guarded block vanish. Verified by comparing the
release image across the extraction: `.text` identical at 36028 bytes, `.data`
identical, and the entire instruction-level difference is one commutative
operand swap the compiler chose (`vfma.f32 s14,s12,s15` → `s14,s15,s12`). `.bss`
grows 8 bytes for the always-present `elsDiagCtx_t`, which relocates later RAM
addresses — so the image is functionally identical, not bit-identical.

`elsDiagCtx_t` sits **last** in `rampsHandler_t` on purpose: carrying it in a
release build then cannot shift the offset of any field above it.

This is dispatch and no-ops, **not a shared trace framework**. With one probe in
existence, factoring out "common" trace machinery would be inventing a seam
rather than finding one. When a second probe lands, whatever genuinely repeats
can move into `els_diag.h` — that is the open loop, not an oversight.

## Registry

### `takeup-settle-v2` — schema 2 — **retained as a worked example; its question is still open**

Measures how long the carriage keeps moving after the ELS take-up finishes, to
put a number on `ELS_SLIP_SETTLE_TICKS`, which until then was a guess.

Capture starts when the take-up completes and ends at the servo's next pulse
(`ELS_DIAG_END_PULSE`) or when the buckets run out (`ELS_DIAG_END_WINDOW`). A
window-full capture *did not finish measuring* — treat its tail as a floor, not a
result.

| Field | Meaning |
|---|---|
| `diagSeq` | increments once per completed capture; edge-detect it, there is deliberately no "in progress" register |
| `diagTrace[50]` | per-bucket **signed** sum of Z counts — signed so encoder dither cancels and real motion does not |
| `diagBucketTicks` | ISR ticks per bucket, published so the host never assumes the ISR rate (this repo has disagreed with itself about that rate by 10×) |
| `diagBucketCount` | populated bucket count |
| `diagSettleTicks` | ticks from capture start to the last tick that saw motion — **the measurement** |
| `diagNetCounts` | signed Z counts across the whole capture |
| `diagCaptureTicks` | how long the capture ran; distinct from `diagSettleTicks`, which is when Z last *moved* |
| `diagEndReason` | `ELS_DIAG_END_*` |

**Result (2026-08-16), downgraded 2026-08-18:** the 13 captures read all-zero
— `settle_ticks` and `net_counts` identically 0 in every row. An earlier
version of this block read that as *"the carriage stops dead; the question is
answered."* An audit of the recorded data
(`els-settle-measurement-findings-2026-08-18.md`) showed the claim cannot be
carried by those captures, for three independent reasons:

1. **The window was half the gate.** `ELS_DIAG_BUCKET_TICKS` was 10, giving a
   500-tick observable span against `ELS_SLIP_SETTLE_TICKS = 1000`. No capture
   could distinguish a good constant from one 10× too large. Fixed 2026-08-18:
   bucket width is now 40 (2000-tick window, 2× the gate). No schema bump —
   bucket width is self-describing via `diagBucketTicks`.

   The capture also lives inside `ELS_TAKEUP_CONFIRM_WINDOW_TICKS`
   (`fw/Core/Src/Ramps.c`) = **25000 ticks ≈ 242.7 ms**, against which it is
   ~12.5× short. Demonstrated, not computed: the "hand nudge long after the
   last pulse" case in `els_takeup_confirm_test.cpp` injects at +5000 ticks —
   deliberately inside the confirm window — and the capture has already
   published before the nudge arrives, so `diagNetCounts` stays 0. **A
   disturbance timed for the confirm gate is invisible to this capture.** Do
   not read a zero as "nothing moved during the take-up confirmation"; it
   speaks only about the settle window.
2. **The recorder of that era discarded `diagEndReason`**, so this section's
   own floor-not-a-result rule is unappliable to all 13 rows — none can be
   classified `END_PULSE` vs `END_WINDOW`. The export gap is closed (the
   recorder now stores `end_reason` and `capture_ticks`); the 13 legacy rows
   stay permanently ambiguous.
3. **The armed window has never demonstrated a nonzero.** "Perfectly still"
   and "not looking during the window" produce identical output. Schema 1's
   nonzero traversal data vouches for the dZ read path itself, but not for
   v2's `takeupPending`-gated window. The next capture session must include a
   condition known to move Z during the window before any zero is trusted.

   **The firmware read path is proven and does NOT need the lathe.** Building
   `Ramps.c` together with `els_takeup_confirm_test.cpp` with the probe
   compiled in and injecting a known 20-count nudge through the real
   `SynchroRefreshTimerIsr()` reads back `diagNetCounts` = 20 exactly
   (2026-08-20). So the ISR → `diagNetCounts` path is live, and the ambiguity
   in the 13 zeros is **only** about whether elspi's encoder was delivering
   counts — not about whether the firmware was looking.

**RETIRED 2026-08-22, and the reason is the whole point of v3: v2 could not
measure a confirmed take-up at all.** Not a tuning miss — a structural one,
found by running the real ISR and printing every tick rather than reasoning
about it:

* The capture starts **one tick after the take-up's last pulse**, so `t=0` was
  never the problem. (A first diagnosis on 2026-08-22 said the capture started
  early and the ramp's overshoot ended it. Half wrong, and worth recording
  because it was about to be *fixed*: moving `t=0` would have changed nothing
  on its own.)
* `ELS_SETTLE_TICKS` (50) later the gate confirms, `applyPhaseCorrection()`
  queues its jog, and that jog's first pulse ends the capture — at **51 ticks**,
  observed with `stepsToGo = -398` and `lastCorrection = -392.76` on the ending
  pulse. Every capture taken on the real lathe on 2026-08-21 reads 59–69 ticks
  for exactly this reason.
* Hold the gate open and a **second** end cause appears underneath: the decel
  ramp overshoots the crossing test and keeps emitting the residual, gaps
  stretching as it slows (observed: 5 steps past target, the last pair 262
  ticks apart), ending the capture at **134 ticks**.

So the 2000-tick window could only ever fill on a **refused** take-up — where
nothing is coupled and there is nothing to measure. That is why all 148
captures read zero, and why no reflash of v2 would have changed it.

### `takeup-settle-v3` — schema 6 — the same trace, with the machine actually held still

Same registers, same geometry, same `settle_ticks` measurement. Two changes,
and **both are needed** — the emulator mutation suite reddens if either is
removed, reproducing exactly the two end causes above (51 ticks, 134 ticks):

1. **A pulse arriving while `takeupPending` is set RE-ARMS the capture** rather
   than ending it, so `t=0` is the take-up's last pulse by construction with no
   need to know in advance which pulse that is. Once the gate releases the
   machine, a pulse ends the capture as before: the pass starting is a real
   end, the ramp finishing is not.
2. **The gate's dwell is held open until the capture publishes**
   (`elsDiagExtraDwell`, added to both the dwell and the confirm-window abort
   threshold so the window after it keeps its length). Bounded by
   `ELS_DIAG_SETTLE_HOLD_CEILING_TICKS` (~78 ms) so a capture that never
   publishes cannot hold the machine.

**`END_WINDOW` now means the opposite of what it meant under v2**, which is why
the schema id changed rather than the constants. Under v2 it meant "the servo
stayed quiet longer than I could watch" — a floor. Under v3 the gate is held
for exactly this window, so a healthy take-up reaches the end of it with the
servo still quiet: **`END_WINDOW` is the complete measurement and
`settle_ticks` is the answer.** An `END_PULSE` under v3 means something drove
the servo during the hold, which the hold exists to prevent — read that trace
as cut short and look for what moved.

**What the diagnostic build deliberately does differently, and must not be read
past.** The gate's first verdict lands ~2000 ticks after the last pulse instead
of 50. Attribution keeps accumulating throughout, so genuinely late motion
inside `ELS_SLIP_SETTLE_TICKS` is still credited and the recovery path still
works — but a refusal reaches the operator ~20 ms later, and a scenario that
places a disturbance *by gate state* rather than by time-since-last-pulse will
behave differently here than in a release build (the emulator's late-motion
regression pair is asserted release-only for exactly this reason). **Never read
"the diagnostic build confirmed" as "release would have confirmed."**

The release image is unaffected: `elsDiagExtraDwell()` is a constant 0
everywhere else, and the release `reflex-fw.bin` is **byte-identical** across
this change (`b3cea4f2…`, 37428 bytes, verified 2026-08-22).

**The finding this probe existed to settle — SETTLED 2026-08-27.** The gate
releases the cut 50 ticks after the last take-up pulse while
`ELS_SLIP_SETTLE_TICKS` said motion may still be attributable for 1000 — two
constants 20× apart that no measurement had adjudicated. The v3 capture session
adjudicated them: 18 captures, all ending `END_WINDOW` at the full 2000 ticks,
**11 completely still** and the other **7 delivering exactly one count each**
(net −1) at 79, 545, 571, 656, 1165, 1399 and 1786 ticks. The carriage stops
essentially dead; the "tail" is a single 5 µm count arriving late.
`ELS_SLIP_SETTLE_TICKS` was lowered **1000 → 700** on that basis — 700 sits in
the empty band between the ≤656 and ≥1165 clumps, so the reduction discards
nothing ever observed while cutting the nudge-attribution window by 30%. The
numbers are executable in `emulator/test/els_slip_horizon_commission_test.cpp`,
so moving the constant without revisiting them turns a test red. **Read the
caveat in that file before extending this**: these captures come from a
diagnostic build whose hold computes the gate's verdict later than release, so
that build is *more permissive* on a marginal take-up — the measurement of the
carriage is valid, a conclusion about what the gate would do in release is not.

v2 is kept in the registry (retired, id burned) rather than deleted: it is the
reference implementation for writing another trace probe, and reflex-ui still
recognises schema 2 so the 148 captures already recorded stay readable.

### `disengage-latch` — schema 3 — **INTERVENING probe, for catching a live defect**

Counts `servoEnableTask` re-asserting `servoMode = 1` while `elsStop.enable == 0`
— i.e. switching a spindle-synced feed back on after a disengage, with the ELS
stop simultaneously disarmed.

**This probe changes behaviour: it REFUSES the re-assert and records that it
did.** Every other probe only observes. The reason is that catching this by
observation alone means letting a carriage actually run away on a lathe with the
stop disarmed — that is not a test, it is the accident. Suppressing makes the
dangerous outcome impossible while the counter still proves whether the condition
arises.

**A non-zero `diagSeq` is the finding.** It means the race occurred and was
caught, not that anything went wrong during the run.

| Field | Meaning (NOT the same as schema 2 — check `diagSchema` first) |
|---|---|
| `diagSeq` | events caught. **The result.** 0 = never happened this run |
| `diagNetCounts` | same count in 32 bits, so a long run cannot wrap unnoticed |
| `diagCaptureTicks` | `elsStop.active` at the most recent event (expect 0) |
| `diagSettleTicks` | `servoMode` at the event: 0 = this would have STARTED a feed |
| `diagEndReason` | 1 once any event has been seen |
| `diagTrace[]` | unused |

Scope of the intervention: suppression is conditional on `enable == 0`, so while
this is compiled in, sync motion started by a non-ELS path also stops being
auto-enabled. Acceptable in a diagnostic build; it is why this must never reach a
release branch.

More sensitive than the end-to-end system test, which only fails when the timing
escalates all the way to visible carriage travel — an A/B over 20 runs showed
zero failures in *both* arms and proved nothing. This fires on the condition.

### `mode-watch-v2` — schema 5 — **durable: rung 1 of the 2026-08-16 architecture direction**

Publishes the firmware-derived machine mode (`els_machine_mode.h`, `ELS_MMODE_*`)
once per `servoEnableTask` tick, and **carries schema 3's intervention forward**:
it still suppresses the `servoEnableTask` re-assert while `elsStop.enable == 0`.
One probe fits a build and a flash costs a physical session (the board does not
run new firmware until power-cycled, which cannot be done remotely) — so the
probe that collects mode data for weeks must also keep the latch counter
running. With the F1/F2 fixes on this branch the counter is **expected to stay
0**; nonzero means some path still leaves sync armed across a disengage, which
is a finding, not noise. Schema 3's non-ELS-sync caveat applies unchanged.

**What v2 changed (and why schema 4 is retired): only the counting.** The
suppression itself is unconditional exactly as before, but `diagNetCounts` now
ticks only when `servoMode` was 0 — the refusal that would have *switched the
feed on*. The 2026-08-17 hardware sessions showed the v1 counter climbing 1719
in an afternoon, every event a no-op refusal during enable-less power feed
(`servoMode` already 1, so the refused assert would have changed nothing); the
one event the counter exists to catch would have been three digits of noise
deep. Under v2 the "expect 0" reading is finally literal: **any nonzero count
is a finding.** Changing what schema 4's numbers meant in place would have
silently corrupted the recorded round-1/2 data — hence the new id, same
discipline as takeup-settle v1 → v2.

The purpose is rung 2: reflex-ui's watchdog compares the UI's model against this
register during normal use, and the divergence log — not anyone's confidence —
decides when the mode becomes a real (protocol-versioned) register and the UI
starts acting on it.

| Field | Meaning (NOT schema 2's, 3's, or 4's — check `diagSchema` first) |
|---|---|
| `diagCaptureTicks` | **current derived mode** (`ELS_MMODE_*`) |
| `diagSettleTicks` | previous mode — the from-side of the last transition |
| `diagSeq` | mode-transition counter; bumped last, so an edge-detected read sees a consistent pair |
| `diagNetCounts` | **effective** latch suppressions (`servoMode` was 0), cumulative. **Expect 0; nonzero is always a finding** |
| `diagEndReason` | 1 once any counted suppression has been seen |
| `diagTrace[0]`, `[1]` | `servoMode` (0 by construction) and `active` at the most recent counted suppression |

Mode values are a wire contract (append, never renumber), pinned as literals in
`els_machine_mode_test` on the firmware side and mirrored by reflex-ui. Known
limitation, deliberate: `HELD` does not split "armed idle" from "stop fired" —
the registers cannot tell them apart today (the `active` overload); publishing
the merged state honestly beats guessing.

`disengage-latch` (schema 3) stays selectable for a pure-latch run with no mode
publication; for the combined agenda this probe supersedes it.

### `mode-watch` — schema 4 — **RETIRED**

Superseded by v2, which counts only the effective suppressions instead of every
refusal — v1's counter climbed by one per 100 ms tick during any enable-less
power feed, which is noise, not signal. Its id is burned and will not be
reused; the round-1/2 hardware data recorded under schema 4 keeps its v1
meaning. Selecting it is a compile error that names the replacement.

### `takeup-settle` — schema 1 — **RETIRED**

Superseded by v2, which ends the capture at the servo's next pulse instead of a
fixed window. Its id is burned and will not be reused. Selecting it is a compile
error that names the replacement.

## Adding a probe

1. **Register the schema id** in `Core/Inc/Ramps.h`, appending after the highest
   existing id. Never renumber. `scripts/lib/diag.sh` parses these defines, so
   the new probe becomes selectable as `--diag=<lowercase-hyphenated-name>`
   automatically — there is no second list to update.
2. **Add an arm to the `#error` chain** in `Ramps.h` so the id is recognised.
   Without it the build refuses, by design.
3. **Write the probe** in `Core/Inc/els_diag_<name>.h`, implementing all five
   entry points, and add an arm to the dispatch `#if` in `els_diag.h` so it gets
   included. Nothing goes in `Ramps.c`: the call sites are already there and are
   probe-agnostic. See "Code layout" above — especially the `elsDiagCapturing`
   guard, which is load-bearing for ISR cost.
4. **Add a test target** in `emulator/CMakeLists.txt` mirroring
   `els_diag_scratch_takeup_settle_v2_test`, and an assertion arm in
   `emulator/test/els_diag_scratch_test.cpp`. Both are load-bearing: the target
   is what compiles your probe at all, and the test file's `#else #error` makes
   "added a probe, wrote no assertions" a build failure. Pin the **literal** wire
   value, not `ELS_DIAG_PROBE` — asserting the published schema equals the macro
   that set it is a tautology that cannot fail.
5. **Mirror it in the UI**, in all the places — mirroring the id alone is not
   enough, and the failure is silent until you are standing at the lathe:
   - `ui/reflex/utils/devices.py` — the schema constant.
   - `ui/reflex/fsms/els_diag.py` — add it to `KNOWN_SCHEMAS`. **This is the one
     that bites.** The recorder refuses any schema outside that set, so a probe
     registered in firmware but missing here makes the UI log *"firmware reports
     diagSchema=N, which this UI does not recognise"* and go dormant. The
     firmware is fine, the flash is fine, and nothing is recorded.
   - `SCHEMAS_WITH_END_REASON` in the same file, if the probe publishes
     `diagEndReason`.

   Nothing cross-checks these two registries yet: the register-map contract test
   compares layout (field sets, offsets, types, total size) and says nothing
   about schema ids. Until that check exists, this step is the check — though
   since the weld both files live in this repository, so the cross-check is now
   a plain test away rather than a cross-repo problem.
6. **Document it here**, including whether it is one-off or durable, and what its
   result was once you have one.

Until 2026-08-16 the diagnostic half of the leak-guard test was never compiled by
anything — the comment claimed both halves existed while only the release target
was built. Step 4 is why that cannot recur: a probe with no target is a probe
nothing tests.

## Retiring a probe

Mark the schema line `/* RETIRED -- see <replacement> */`. The scripts parse that
comment and drop it from the selectable list, and the `#error` chain should name
the replacement. Leave the id in place forever.

Retiring a probe does **not** mean deleting its code. A little residue is fine as
long as it cannot activate in a release build — which the guards above already
guarantee. Delete it when it stops being a useful example, not on a schedule.
