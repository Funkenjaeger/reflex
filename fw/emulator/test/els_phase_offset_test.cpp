/*
 * Phase-offset primitive tests (landed 2026-08-21; designed 2026-08-14, INV7).
 *
 * Pins the behavior of the offsetSteps parameter on
 * elsComputePhaseCorrection() (Core/Inc/els_phase.h), added to support
 * TickTick 6a77c5988f0854e3485e4369 (auto-advance from X depth) and
 * 6a77c5b28f08c5e690294e7e (manual fixed-distance advance). See the PHASE
 * OFFSET note in els_phase.h and the phase-offset section of todo.md.
 *
 * Style matches els_phase_test.cpp: pure host build, no HAL/struct deps,
 * fixed 1:1 geometry (threadPitchSteps == zCountsPerPitch == PITCH) chosen so
 * deltaZ can inject an exact phaseError, isolating the offset math from the
 * idealAdvance/actualAdvance conversion already covered by els_phase_test.
 *
 * Build/run: the `els_phase_offset_test` CTest target (emulator/CMakeLists.txt).
 */
#include "els_phase.h"

#include <cstdio>
#include <cmath>
#include <cstdlib>

static int g_failures = 0;

#define CHECK(cond, msg) do { \
  if (!(cond)) { \
    std::printf("  [FAIL] %s (%s:%d)\n", msg, __FILE__, __LINE__); \
    g_failures++; \
  } else { \
    std::printf("  [PASS] %s\n", msg); \
  } \
} while (0)

static const float PITCH = 1000.0f;

/* deltaSpindle=0, droSign=+1 (stopDirection=1, cuttingDir=+1 since
 * syncRatioNum=1>0), 1:1 pitch ratio => phaseError == offsetSteps exactly
 * when deltaZ == 0. This isolates the NEW term from the existing
 * idealAdvance/actualAdvance math (already covered by els_phase_test.cpp). */
static elsCorrResult_t correctionForOffset(int32_t offsetSteps) {
  return elsComputePhaseCorrection(
      /*deltaSpindle=*/0, /*deltaZ=*/0,
      /*syncRatioNum=*/1, /*syncRatioDen=*/6,
      /*threadPitchSteps=*/PITCH, /*zCountsPerPitch=*/PITCH,
      /*stopDirection=*/1,
      offsetSteps);
}

int main() {
  std::printf("=== ELS phase-offset design-validation tests ===\n");

  /* T1: offset=0 is an EXACT regression — existing behaviour (no offset
   * field written) must be bit-for-bit unchanged. This is the test that
   * MUST stay green under every mutation below; it is the safety net for
   * "default off" (6a77c598/6a77c5b2 both require this). */
  {
    elsCorrResult_t r = correctionForOffset(0);
    CHECK(r.stepsToAdd == 0, "T1: offsetSteps=0 is a true no-op");
  }

  /* T2: a small offset within the fold's linear region passes through
   * unchanged in magnitude (mirrors the empirical table in the design doc). */
  {
    elsCorrResult_t r = correctionForOffset(332); /* ~0.33*pitch, the 6a77c598 worst case */
    CHECK(r.stepsToAdd == 332, "T2: sub-half-pitch offset (0.33*pitch, 6a77c598 worst case) passes through unfolded");
  }

  /* T3: an offset of EXACTLY one pitch folds to a no-op. This is the
   * open question in 6a77c5b2 ("does a full pitch of offset land in the
   * same place") answered by the math, not by taste: YES, by construction
   * of the existing fmodf(...,pitch) fold that already existed before this
   * feature and is untouched by it. */
  {
    elsCorrResult_t r = correctionForOffset((int32_t)PITCH);
    CHECK(r.stepsToAdd == 0, "T3: offset == 1 pitch aliases to a no-op (mod-pitch fold)");
  }

  /* T4: an offset of 1.5 pitches aliases to the SAME correction as an
   * offset of 0.5 pitches -- the UI-facing claim that must follow: entering
   * more than one pitch of offset is not merely wasted, it is
   * INDISTINGUISHABLE on the machine from entering (offset mod pitch). If
   * the UI ever lets the running total exceed one pitch silently, the
   * operator's mental model of "how wide is my groove" silently diverges
   * from the machine's. */
  {
    elsCorrResult_t r1 = correctionForOffset((int32_t)(1.5f * PITCH));
    elsCorrResult_t r2 = correctionForOffset((int32_t)(0.5f * PITCH));
    CHECK(r1.stepsToAdd == r2.stepsToAdd,
          "T4: offset=1.5*pitch aliases identically to offset=0.5*pitch");
  }

  /* T5: THE ASYMMETRY FINDING. A negative offset does NOT produce a small
   * backward jog -- the existing forward-bias (Core/Inc/els_phase.h, "never
   * unload the lash the takeup just took up") reinterprets it as
   * (pitch - |offset|) in the CUTTING direction. Verified against the real
   * production fold+bias code, not asserted from reading. This matters for
   * 6a77c5b2's "cumulative, nudge and un-nudge" workflow: decrementing the
   * running offset does not step the tool back by a little, it jogs forward
   * by almost a whole pitch. Document this for the UI design; do not let the
   * UI imply symmetric +/- nudging without warning. */
  {
    elsCorrResult_t r = correctionForOffset(-100);
    CHECK(r.stepsToAdd == 900,
          "T5: offset=-100 forward-biases to +900 (pitch-|offset|), NOT -100 -- asymmetric by design");
  }

  /* T6 (mutation-catcher): offset applied AFTER the fold instead of before
   * would behave identically to T1-T2 (small offsets) but diverge at T3/T4
   * (fold boundary cases). This test's REASON to exist is to make that
   * mutation observable -- see the mutation notes in the design doc. It is
   * the same assertion as T3 restated for clarity as a named mutation
   * target. */
  {
    elsCorrResult_t r = correctionForOffset(2 * (int32_t)PITCH + 1);
    CHECK(r.stepsToAdd == 1,
          "T6: offset=2*pitch+1 folds to 1 (proves pre-fold placement, catches post-fold-addition mutation)");
  }

  std::printf(g_failures == 0 ? "=== ALL PASS ===\n" : "=== %d FAILURE(S) ===\n", g_failures);
  return g_failures == 0 ? 0 : 1;
}
