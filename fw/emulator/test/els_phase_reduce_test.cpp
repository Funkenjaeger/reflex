/*
 * EQUIVALENCE SWEEP for the deltaSpindle mod-PPR reduction (els_phase.h,
 * elsReduceDeltaSpindle, landed 2026-08-27 as phase shape (a)).
 *
 * WHAT IS BEING MEASURED. elsComputePhaseCorrection widens deltaSpindle -- the
 * spindle counts accumulated since the phase reference was LATCHED, which grows
 * for the whole job -- to float32 and multiplies. Above 2^23 a float32's ULP
 * exceeds one leadscrew step, so idealAdvance quantizes coarsely and the fold
 * that follows lands in the wrong groove. The fix reduces deltaSpindle modulo
 * one encoder revolution BEFORE the widening: one spindle revolution advances
 * the tool exactly one pitch, so every reduced value names the SAME groove.
 *
 * TWO ORACLES, AND WHY BOTH ARE NEEDED. The first version of this sweep used
 * only oracle B and reported the patched path as 27% "wrong", which was an
 * artefact of the reference, not a defect: oracle B accumulates the EXACT
 * advance and then folds modulo the ROUNDED threadPitchSteps register, so it
 * carries its own drift of (true pitch - rounded pitch) per revolution -- the
 * very error the reduction removes. Measuring the fix against a reference that
 * contains the fault reads the fix as the fault.
 *
 *   ORACLE A -- TRUE GEOMETRY, exact rationals in double. What the tool
 *               physically should do. This is the ground truth for "did we land
 *               in the right groove", and the only one a safety claim may rest
 *               on.
 *   ORACLE B -- the pre-patch FORMULA at double precision, unreduced, fed the
 *               same rounded register the firmware holds. Not a truth: a
 *               BEHAVIOUR-CHANGE reference, which is what makes the residual
 *               below measurable rather than asserted.
 *
 * ERRORS ARE CLASSIFIED BY SEVERITY, because "differs" conflates two things
 * that are nothing alike. One leadscrew step is 127/64000 mm = 2.0 um; a fold
 * that lands in the wrong groove is a whole pitch, 1.41 mm at 18 TPI -- a
 * scrapped thread. A test that counted both as one "mismatch" would rate a
 * 2 um rounding difference as equal to a ruined part.
 *
 * GEOMETRY IS elspi's, not invented: 12800 leadscrew steps/inch
 * (servo_ratio = 127/64000 mm/step), 6144 PPR spindle encoder, 200 count/mm Z
 * scale. syncRatioNum/Den are what reflex-ui's axis.py _set_sync_ratio would
 * push -- a REDUCED Fraction, which is exactly why PPR is not recoverable as an
 * integer from them. 18 TPI makes threadPitchSteps 64000/90 = 711.111... (NOT
 * representable in float32); 4 TPI makes it 3200.0 (exactly representable), so
 * the pair brackets the residual: 4 TPI is the control where it must vanish.
 *
 * Build/run: the `els_phase_reduce_test` CTest target. Header-only, like
 * els_phase_test -- no HAL, no firmware sources.
 */
#include "els_phase.h"

#include <cstdio>
#include <cmath>
#include <cstdint>
#include <cstdlib>

/* ---- the pre-patch body, copied verbatim minus the reduction ------------- */
static int32_t unpatchedSteps(int32_t deltaSpindle, int32_t deltaZ,
                              int32_t syncRatioNum, int32_t syncRatioDen,
                              float threadPitchSteps, float zCountsPerPitch,
                              int16_t stopDirection, int32_t offsetSteps)
{
  float idealAdvance  = (float)deltaSpindle * (float)syncRatioNum / (float)syncRatioDen;
  float actualAdvance = (float)deltaZ * threadPitchSteps / zCountsPerPitch;

  int32_t cuttingDir = (syncRatioNum > 0) ? 1 : -1;
  if (threadPitchSteps * zCountsPerPitch < 0.0f) cuttingDir = -cuttingDir;

  int32_t droSign = (int32_t)stopDirection * cuttingDir;
  float phaseError = idealAdvance - (float)droSign * actualAdvance + (float)offsetSteps;

  float pitch      = threadPitchSteps;
  float correction = fmodf(phaseError, pitch);
  if (correction >  pitch / 2.0f) correction -= pitch;
  if (correction < -pitch / 2.0f) correction += pitch;
  if ((float)cuttingDir * correction < 0.0f) correction += (float)cuttingDir * pitch;
  return (int32_t)lroundf(correction);
}

/*
 * The shared double-precision body for both oracles. The ONLY difference
 * between them is which pitch/ratio values are handed in: oracle A gets the
 * true rationals, oracle B gets the rounded float32 registers widened.
 */
static int32_t oracleSteps(int32_t deltaSpindle, int32_t deltaZ,
                           double ratio,      /* leadscrew steps per spindle count */
                           double tps,        /* leadscrew steps per pitch (the fold modulus) */
                           double zcpp,       /* Z counts per pitch */
                           int32_t cuttingDir, int16_t stopDirection, int32_t offsetSteps)
{
  double idealAdvance  = (double)deltaSpindle * ratio;
  double actualAdvance = (double)deltaZ * tps / zcpp;

  int32_t droSign = (int32_t)stopDirection * cuttingDir;
  double phaseError = idealAdvance - (double)droSign * actualAdvance + (double)offsetSteps;

  double correction = fmod(phaseError, tps);
  if (correction >  tps / 2.0) correction -= tps;
  if (correction < -tps / 2.0) correction += tps;
  if ((double)cuttingDir * correction < 0.0) correction += (double)cuttingDir * tps;
  return (int32_t)llround(correction);
}

struct Geom {
  const char *name;
  int32_t num, den;         /* the REDUCED sync fraction the UI pushes */
  float   tps, zcpp;        /* the float32 registers the firmware holds */
  double  tpsTrue, zcppTrue;/* the exact rationals the machine actually has */
  int64_t ppr;
};

/* elspi: servo 127/64000 mm/step, encoder 6144 PPR, Z scale 1/200 mm/count.
 *
 * THE PPR WAS 1000 HERE UNTIL 2026-08-28, AND 1000 IS NOT THIS MACHINE. It is
 * InputDispatcher.encoder_ppr's class default (ui/reflex/dispatchers/input.py),
 * which is what an unconfigured build reports and what the stale heritage
 * config under ~/.config/rotary-controller-python still carries. elspi's live
 * config is elsewhere -- REFLEX_CONFIG_DIR=/var/lib/reflex-config, set by
 * deploy/start.sh because the service runs as root -- and CoordBar-0.yaml there
 * says encoder_ppr: 6144. Confirmed against the machine rather than against the
 * config: elspi's own diag/phase_live.jsonl captures carry syncRatioNum 25,
 * syncRatioDen 216, threadPitchSteps 711.111083984375, which is 6144 * 25/216
 * to the float32 and matches ui/AGENTS.md's commissioned table.
 *
 * The sweep still ran, still passed, and still measured a residual -- of a
 * machine that does not exist. The 1000-PPR sync fractions were internally
 * consistent (1000 * 32/45 = 711.111 too), which is exactly why nothing here
 * went red for it: a self-consistent wrong geometry produces confident numbers.
 * Every figure this file printed before 2026-08-28 should be re-read as being
 * about that other machine.
 */
static const Geom GEOMS[] = {
  /* 18 TPI: pitch 127/90 mm -> tps 64000/90 = 711.111..., sync 25/216,
   * zcpp 25400/90 = 282.222...  threadPitchSteps is NOT exact in float32. */
  { "18 TPI (tps 711.111, inexact)", 25, 216,
    (float)(64000.0 / 90.0), (float)(25400.0 / 90.0),
    64000.0 / 90.0, 25400.0 / 90.0, 6144 },
  /* 4 TPI: pitch 127/20 mm -> tps 3200.0 EXACT, sync 25/48, zcpp 1270.0.
   * The control: with an exact pitch the residual has nothing to come from. */
  { "4 TPI  (tps 3200.0, exact)",    25,  48,
    3200.0f, 1270.0f,
    3200.0, 1270.0, 6144 },
};

/* Retract positions swept per deltaSpindle, in Z counts (200/mm), covering a
 * realistic carriage range in both directions plus the zero control. */
static const int32_t DZ[]  = { 0, 811, -811, 20011, -20011, 97001, -97001 };
/* Phase offset: 0 is the pre-feature path; the nonzero value proves the
 * reduction does not interact with the offset term. */
static const int32_t OFF[] = { 0, 137 };

/*
 * TWO REGIMES, because "how bad is it" has two different honest answers and
 * quoting only one of them would be a choice dressed up as a measurement.
 *
 *   LONG JOB   -- 122,880,000 counts = 20,000 revolutions at 6144 PPR, about 67
 *                 minutes of continuous rotation at 300 rpm. What a real part
 *                 can plausibly reach.
 *
 *                 THE REGIME IS DEFINED IN REVOLUTIONS, NOT COUNTS, and the
 *                 count is derived. It was a literal 20,000,000 until
 *                 2026-08-28, chosen when this file assumed 1000 PPR -- so
 *                 correcting PPR to 6144 would have quietly shrunk "a 20,000
 *                 revolution job" to 3,255 revolutions while the label kept
 *                 saying 20,000. A regime whose physical meaning moves when an
 *                 unrelated constant is fixed is not a regime.
 *   INT32 SPAN -- 2,100,000,000 counts = 341,796 revolutions, i.e. the
 *                 whole range of the int32 deltaSpindle actually is. Nothing in
 *                 the firmware clamps or rewraps it, so this is reachable in
 *                 principle and is where the float32 ULP genuinely dominates.
 *                 Included because a defect bounded only by "nobody runs a job
 *                 that long" is bounded by a habit, not by the code.
 *
 * Sample count is held constant between them so the percentages compare.
 *
 * EVERY STRIDE MUST BE COPRIME WITH PPR, and it is checked at runtime below
 * rather than left to the comment. The first draft of this file used 145000 for
 * the wide regime -- a multiple of the PPR of 1000 it then assumed, so
 * (ds mod PPR) was 0 at EVERY sample and the sweep measured one single phase
 * within the revolution while reporting 202,762 samples. It produced a
 * confident, entirely meaningless 0.4444%. A stride that silently collapses the
 * sample space is the exact shape of a check that cannot fail, so the property
 * is now asserted. It is asserted against whatever PPR the geometry carries,
 * which is why correcting 1000 -> 6144 could not resurrect that bug silently:
 * 6144 = 2^11 * 3, so both strides (617, prime; 144997, odd and not a multiple
 * of 3) still qualify, and the runtime check says so rather than this comment.
 */
struct Regime { const char *name; int64_t dsMax, dsStride; };
static const Regime REGIMES[] = {
  { "long job  (20k rev)",   122880000LL,  617LL    },
  { "int32 span(342k rev)",  2100000000LL, 144997LL },
};

static int64_t gcd64(int64_t a, int64_t b) { return b ? gcd64(b, a % b) : a; }

/* Severity buckets. GROSS is the safety-relevant one: an error past a quarter
 * pitch is a fold that chose a different groove. */
struct Tally {
  int64_t n, differ, minor, moderate, gross;
  int64_t worst;
};
static void tally(Tally &t, int64_t err, double pitch)
{
  t.n++;
  if (err == 0) return;
  t.differ++;
  if (llabs(err) > llabs(t.worst)) t.worst = err;
  if (llabs(err) <= 2)                        t.minor++;
  else if ((double)llabs(err) <= pitch / 4.0) t.moderate++;
  else                                        t.gross++;
}
static void report(const char *label, const Tally &t)
{
  printf("      %-22s differ=%-8lld (%.4f%%)  minor<=2st=%-8lld moderate=%-6lld "
         "GROSS=%-8lld (%.4f%%)  worst=%+lld\n",
         label, (long long)t.differ, 100.0 * (double)t.differ / (double)t.n,
         (long long)t.minor, (long long)t.moderate,
         (long long)t.gross, 100.0 * (double)t.gross / (double)t.n,
         (long long)t.worst);
}

int main(void)
{
  int failures = 0;

  /* ---- T1: the reduction primitive itself -------------------------------- */
  printf("--- T1 reduction primitive ---\n");
  for (const Geom &g : GEOMS) {
    int64_t bad = 0, negIn = 0;
    for (int64_t d = -5000000; d <= 5000000; d += 997) {
      int32_t r = elsReduceDeltaSpindleBy((int32_t)d,
                                         elsComputeSpindlePeriod(g.num, g.den, g.tps));
      if (d < 0) negIn++;
      /* Must land in [0, PPR) -- the ((d%P)+P)%P form, never C truncating %. */
      if (r < 0 || r >= (int32_t)g.ppr) bad++;
      /* Must name the same groove: r == d (mod PPR). */
      int64_t want = ((d % g.ppr) + g.ppr) % g.ppr;
      if ((int64_t)r != want) bad++;
    }
    bool ok = (bad == 0) && (negIn > 0);
    printf("  [%s] %-32s out-of-range/wrong-residue=%lld  negative inputs swept=%lld\n",
           ok ? "PASS" : "FAIL", g.name, (long long)bad, (long long)negIn);
    if (!ok) failures++;
  }

  /* The period recovered from the rounded register must BE the true PPR. This
   * is the residual's own guard: if the rounding ever moved P off the integer,
   * every claim above collapses, so it is asserted rather than assumed. */
  for (const Geom &g : GEOMS) {
    int32_t P = elsComputeSpindlePeriod(g.num, g.den, g.tps);
    int32_t r = elsReduceDeltaSpindleBy((int32_t)g.ppr, P);
    /* Two claims, not one: the period the double math recovered must BE the
     * true PPR, and one whole revolution must therefore reduce to zero. The
     * second alone would also pass if P came back 0 (do-not-reduce) and g.ppr
     * happened to be 0 -- it is not, but asserting P explicitly means this
     * cannot start passing for that reason later. */
    bool ok = (P == (int32_t)g.ppr) && (r == 0);
    printf("  [%s] %-32s recovered period == PPR (%lld)\n",
           ok ? "PASS" : "FAIL", g.name, (long long)g.ppr);
    if (!ok) failures++;
  }

  /* Turning (num == 0) and degenerate pitch must be exact no-ops. */
  {
    /* Both degenerate configs must yield a period of 0, and 0 must mean the
     * reduction is a no-op. Checked as the two separate facts they are. */
    bool ok = (elsComputeSpindlePeriod(0, 45, 711.111f) == 0)
           && (elsComputeSpindlePeriod(32, 45, 0.0f) == 0)
           && (elsReduceDeltaSpindleBy(123456789, 0) == 123456789)
           && (elsReduceDeltaSpindleBy(-123456789, 0) == -123456789);
    printf("  [%s] turning / degenerate config is a no-op\n", ok ? "PASS" : "FAIL");
    if (!ok) failures++;
  }

  /* ---- T1b: the sweep must actually sweep the phase ---------------------- */
  for (const Regime &reg : REGIMES) {
    for (const Geom &g : GEOMS) {
      bool ok = (gcd64(reg.dsStride, g.ppr) == 1);
      printf("  [%s] stride %lld is coprime with PPR %lld (%s)\n",
             ok ? "PASS" : "FAIL", (long long)reg.dsStride, (long long)g.ppr, reg.name);
      if (!ok) failures++;
    }
  }

  /* ---- T2: the sweep ----------------------------------------------------- */
  printf("--- T2 severity sweep (A = true geometry, B = pre-patch formula in double) ---\n");
  int64_t onsetB = -1;   /* first revolution count where patched diverges from B */

  for (const Regime &reg : REGIMES) {
    Tally allPA = {0,0,0,0,0,0}, allUA = {0,0,0,0,0,0}, allPB = {0,0,0,0,0,0};
    Tally ctlPA = {0,0,0,0,0,0}, ctlPB = {0,0,0,0,0,0};   /* the 4 TPI control */

    printf("=== regime: %s  (deltaSpindle 0..%lld step %lld) ===\n",
           reg.name, (long long)reg.dsMax, (long long)reg.dsStride);

    for (const Geom &g : GEOMS) {
      /* The sync FRACTION is exact in both oracles -- it is a pair of integers.
       * The PITCH is what differs: true rational vs rounded float32 register. */
      double ratio = (double)g.num / (double)g.den;

      for (int si = 0; si < 2; si++) {
        int16_t sd = (si == 0) ? (int16_t)1 : (int16_t)-1;

        int32_t cuttingDir = (g.num > 0) ? 1 : -1;
        if (g.tps * g.zcpp < 0.0f) cuttingDir = -cuttingDir;

        Tally pa = {0,0,0,0,0,0}, ua = {0,0,0,0,0,0}, pb = {0,0,0,0,0,0};

        for (int64_t ds = 0; ds <= reg.dsMax; ds += reg.dsStride) {
          for (int32_t dz : DZ) {
            for (int32_t off : OFF) {
              int32_t oa = oracleSteps((int32_t)ds, dz, ratio,
                                       g.tpsTrue, g.zcppTrue, cuttingDir, sd, off);
              int32_t ob = oracleSteps((int32_t)ds, dz, ratio,
                                       (double)g.tps, (double)g.zcpp, cuttingDir, sd, off);
              elsCorrResult_t p = elsComputePhaseCorrection(
                  (int32_t)ds, dz, g.num, g.den, g.tps, g.zcpp, sd, off,
                  elsComputeSpindlePeriod(g.num, g.den, g.tps));
              int32_t u = unpatchedSteps((int32_t)ds, dz, g.num, g.den, g.tps, g.zcpp, sd, off);

              tally(pa, (int64_t)p.stepsToAdd - oa, g.tpsTrue);
              tally(ua, (int64_t)u            - oa, g.tpsTrue);
              tally(pb, (int64_t)p.stepsToAdd - ob, g.tpsTrue);

              if (g.tps != 3200.0f && p.stepsToAdd != ob && onsetB < 0)
                onsetB = ds / g.ppr;
            }
          }
        }

        printf("  %-32s stopDirection=%+d  n=%lld\n", g.name, (int)sd, (long long)pa.n);
        report("patched   vs A(true)", pa);
        report("unpatched vs A(true)", ua);
        report("patched   vs B(formula)", pb);

        Tally *dstA = (g.tps == 3200.0f) ? &ctlPA : &allPA;
        Tally *dstB = (g.tps == 3200.0f) ? &ctlPB : &allPB;
        for (int k = 0; k < 2; k++) {
          Tally *d = (k == 0) ? dstA : dstB;
          const Tally *s = (k == 0) ? &pa : &pb;
          d->n += s->n; d->differ += s->differ; d->minor += s->minor;
          d->moderate += s->moderate; d->gross += s->gross;
          if (llabs(s->worst) > llabs(d->worst)) d->worst = s->worst;
        }
        if (g.tps != 3200.0f) {
          allUA.n += ua.n; allUA.differ += ua.differ; allUA.minor += ua.minor;
          allUA.moderate += ua.moderate; allUA.gross += ua.gross;
          if (llabs(ua.worst) > llabs(allUA.worst)) allUA.worst = ua.worst;
        }
      }
    }

    /* ---- the verdict, per regime ----------------------------------------- */
    double grossP = 100.0 * (double)allPA.gross / (double)allPA.n;
    double grossU = 100.0 * (double)allUA.gross / (double)allUA.n;
    printf("  -- 18 TPI verdict: patched GROSS %.4f%%  unpatched GROSS %.4f%%\n",
           grossP, grossU);

    /* POSITIVE CONTROL. Every clean result below is vacuous unless the sweep
     * actually reaches the defect, so this is asserted, not assumed. */
    if (!(allUA.gross > 0)) {
      printf("  [FAIL] unpatched produced no GROSS errors -- sweep not exercising the defect\n");
      failures++;
    }
    /* THE CLAIM SHAPE
     * (a) WAS APPROVED ON. Not "the residual is zero" -- Evan shipped this
     * knowing it is not, and a test demanding zero would be a test nobody could
     * keep green honestly. What must hold is that wrong-groove errors drop by a
     * large FACTOR, and that what survives stays under a stated ceiling. Both
     * bounds are one-sided, so loosening either to pass shows up in the diff. */
    if (!(grossP * 20.0 < grossU)) {
      printf("  [FAIL] patched GROSS %.4f%% is not >=20x better than unpatched %.4f%%\n",
             grossP, grossU);
      failures++;
    }
    if (!(grossP < 0.01)) {
      printf("  [FAIL] patched GROSS %.4f%% exceeds the 0.01%% residual ceiling\n", grossP);
      failures++;
    }
    /* THE CONTROL THAT PINS THE RESIDUAL'S CAUSE. At 4 TPI threadPitchSteps is
     * exactly representable, so if the wrong-groove residual really comes from
     * the ROUNDED pitch register then it must vanish here -- exactly zero, not
     * merely better -- against BOTH oracles.
     *
     * THIS ASSERTION WAS "differ == 0" AND IT WENT RED ON 2026-08-28, when the
     * geometry above was corrected from the 1000 PPR class default to elspi's
     * real 6144. Per its own instruction the explanation was re-examined rather
     * than the bound loosened, and it split in two:
     *
     *   GROSS errors (wrong groove, > pitch/4) DO vanish at exact pitch -- 0 in
     *   both regimes, both oracles, 2.8M samples. The documented cause holds
     *   and els_phase.h is right about the part that scraps a thread.
     *
     *   ONE-STEP differences DO NOT vanish: 0.30% of samples, worst +/-1, at 4
     *   TPI just as at 18. They therefore CANNOT come from the rounded pitch
     *   register, because there is nothing to round here. They are float32
     *   cancellation in the advance terms plus round-to-nearest landing either
     *   side of a half-step, which no geometry removes. The old assertion
     *   folded these two into one number and read the sum as evidence about the
     *   register; at 1000 PPR the sum happened to be zero and the conflation
     *   never showed.
     *
     * So the control is now stated as the two claims it can actually support.
     * Both are one-sided: a gross error appearing, or the noise floor rising
     * above one step, still fails here. */
    bool ctlGrossClean = (ctlPA.gross == 0 && ctlPB.gross == 0);
    bool ctlNoiseBound = (llabs(ctlPA.worst) <= 1 && llabs(ctlPB.worst) <= 1);
    if (!ctlGrossClean) {
      printf("  [FAIL] 4 TPI control (exact pitch) has GROSS errors: "
             "vsA gross=%lld, vsB gross=%lld -- the cause documented in "
             "els_phase.h is wrong and must be rewritten\n",
             (long long)ctlPA.gross, (long long)ctlPB.gross);
      failures++;
    }
    if (!ctlNoiseBound) {
      printf("  [FAIL] 4 TPI control noise floor exceeds one step: "
             "vsA worst=%lld, vsB worst=%lld\n",
             (long long)ctlPA.worst, (long long)ctlPB.worst);
      failures++;
    }
    if (ctlGrossClean && ctlNoiseBound) {
      printf("  -- 4 TPI control (exact pitch): 0 GROSS vs both oracles as "
             "documented; %lld one-step diffs remain (float32 noise, not the "
             "pitch register)\n", (long long)ctlPA.differ);
    }
  }

  printf("  residual onset: patched first differs from oracle B at ~%lld revolutions\n",
         (long long)onsetB);
  printf("=== %s ===\n", failures == 0 ? "ALL PASS" : "FAILURES");
  return failures == 0 ? 0 : 1;
}
