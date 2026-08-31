/*
 * Lathe physics model.
 *
 * Simulates spindle with inertia, leadscrew, carriage with half-nut, and cross-slide.
 */
#ifndef EMU_PHYSICS_H
#define EMU_PHYSICS_H

#include "config.h"
#include <cstdint>
#include <atomic>
#include <cmath>
#include <random>

class LathePhysics {
public:
    explicit LathePhysics(const EmuConfig &cfg);

    /* Called each ISR tick BEFORE calling into firmware.
     * Updates spindle and feeds encoder counter values into emu_hw. */
    /* shared must point to the firmware's rampsSharedData_t for reading servo state */
    void tick(double dt_seconds, const void *shared_data);

    /* Called from GPIO shim when a STEP rising edge is detected. */
    void onStepPulse(int direction);

    /* --- Spindle control (from dashboard keyboard) --- */
    void setTargetRPM(double rpm);
    void toggleDirection();
    void emergencyStop();

    double getSpindleRPM() const { return spindle_omega * 60.0 / (2.0 * M_PI); }
    double getTargetRPM() const { return spindle_target_rpm; }
    bool   getSpindleCW() const { return spindle_target_rpm >= 0; }
    int64_t getSpindleEncoderCounts() const;

    /* --- Half-nut --- */
    enum HalfNutState { DISENGAGED, ENGAGING, ENGAGED };

    void requestHalfNutToggle();

    /* Explicit-state request, for callers that know the state they want rather
     * than the transition they want (the stdin command channel; system tests).
     *
     * IDEMPOTENT, and that is the entire reason it exists rather than callers
     * poking requestHalfNutToggle() twice: a toggle issued while an engage is
     * still waiting for phase alignment CANCELS it (see requestHalfNutToggle),
     * so "close it" expressed as a toggle is state-dependent and would
     * sometimes mean "give up". A test that has to know the current state in
     * order to ask for the next one is a test that can silently ask for the
     * opposite. */
    void setHalfNutEngaged(bool engaged);

    HalfNutState getHalfNutState() const { return half_nut_state; }

    /* --- Carriage (Z-axis) manual move --- */
    double getCarriageMM() const { return carriage_mm; }
    void   jogCarriage(int direction);       /* Arrow key jog: direction +1/-1 */
    void   moveCarriageTo(double target_mm); /* Move to specific position */
    bool   isZMoveTargetActive() const { return z_move_active; }
    int64_t getCarriageEncoderCounts() const;

    /* --- Cross-slide (X-axis) manual move --- */
    double getCrossSlideMM() const { return cross_slide_mm; }
    void   jogCrossSlide(int direction);
    void   moveCrossSlideTo(double target_mm);
    bool   isXMoveTargetActive() const { return x_move_active; }
    int64_t getCrossSlideEncoderCounts() const;

    /* --- Jog status --- */
    bool isZJogging() const { return std::abs(z_jog_velocity) > 0.01; }
    bool isXJogging() const { return std::abs(x_jog_velocity) > 0.01; }

    /* --- Leadscrew --- */
    double getLeadscrewPositionMM() const { return leadscrew_position_mm; }
    double getLeadscrewGridSpacingMM() const { return leadscrew_grid_spacing_mm; }

    /* Nearest carriage position that sits exactly on the leadscrew thread
     * lattice for the CURRENT leadscrew position. Not clamped to travel
     * limits: a target within half a grid of z_min/z_max can end up clamped
     * off-lattice by moveCarriageTo — acceptable edge case. */
    double nearestGridPositionMM(double target_mm) const;

    /* Lash window position: 0 = on -wall, z_backlash_mm = on +wall. Used by the
     * ELS scenario to verify the takeup loads the correct (cutting-side) wall. */
    double getBacklashOffsetMM() const { return backlash_offset; }

    /* --- Carriage settle (servo-driven motion only) --- */

    /* Set the settle time constant at runtime. Exists for TESTS: the settle
     * regime under investigation (is the real tail longer or shorter than the
     * firmware's ELS_SETTLE_TICKS dwell?) is exactly what is unmeasured, so a
     * test that wants one regime or the other must say so out loud rather than
     * inherit it from a config default that has no right to an opinion.
     * <= 0 restores instantaneous carriage response bit-for-bit. */
    void   setSettleTauS(double tau_s) { z_settle_tau_s = tau_s; }
    double getSettleTauS() const { return z_settle_tau_s; }

    /* Displacement the drivetrain has been COMMANDED but has not yet delivered
     * to the carriage, in mm. Signed, same sense as carriage_mm. This is the
     * settle tail itself: nonzero means Z counts are still on their way. */
    double getPendingSettleMM() const { return z_settle_pending_mm; }

    /* Same quantity in Z ENCODER COUNTS, which is the only unit the firmware
     * can reason in. Use this, not the mm form, whenever the question is "can
     * the machine still see this?" — a tail below one count is real carriage
     * motion that no encoder edge will ever report. */
    double getPendingSettleCounts() const {
        return z_settle_pending_mm * z_counts_per_mm;
    }

    /* Is the carriage still moving from servo-commanded motion AT ALL? Decidable
     * rather than asymptotic: the relaxation is flushed once the remainder falls
     * below SETTLE_RESIDUAL_COUNTS, so this goes false in finite time.
     *
     * Note this is the MODEL's answer, not the machine's: it stays true through
     * the sub-count crawl at the very end of the tail, which no encoder reports.
     * Tests asking what the firmware could have observed want
     * getPendingSettleCounts() >= 1 instead. "Has it STOPPED?" is the question
     * the take-up gate never asks, which is what makes both forms worth having. */
    bool   isCarriageSettling() const {
        return z_settle_pending_mm != 0.0;
    }

private:
    /* Config */
    double spindle_inertia;
    double spindle_max_torque;
    double spindle_friction;
    int    spindle_counts_per_rev;
    double leadscrew_mm_per_step;
    double leadscrew_tpi;
    double leadscrew_grid_spacing_mm;  /* 25.4 / tpi */
    double z_counts_per_mm;
    double z_backlash_mm;
    double z_max_mm, z_min_mm;
    double x_counts_per_mm;
    double x_max_mm, x_min_mm;
    double x_manual_step_mm;

    /* PHYSICAL wiring signs (+1/-1), independent of the firmware canonicalization
     * registers (scaleDir/servoDir) that reflex-ui writes. These model how the
     * real machine happens to be wired -- encoder cable orientation / motor
     * wiring -- which the operator's UI reversing toggle must cancel. Applied in
     * the encoder-count getters and onStepPulse; NOT the same as the Modbus
     * scaleDir/servoDir registers (which the host overwrites on connect). */
    int    spindle_scale_sign;
    int    z_scale_sign;
    int    x_scale_sign;
    int    servo_sign;

    /* Spindle state */
    double spindle_theta;         /* cumulative angle in radians */
    double spindle_omega;         /* angular velocity in rad/s */
    double spindle_target_rpm;

    /* Leadscrew state (always tracks stepper steps) */
    double leadscrew_position_mm; /* cumulative from step pulses */
    int64_t leadscrew_total_steps;

    /* Carriage state */
    double carriage_mm;
    HalfNutState half_nut_state;
    bool   half_nut_request_pending;
    double backlash_offset;    /* nut position within play window: [0, z_backlash_mm] */

    /* --- Carriage settle model (added 2026-08-22) -------------------------
     *
     * WHAT IT MODELS. Until now onStepPulse() moved carriage_mm the instant the
     * backlash nut hit a wall, so the simulated carriage had no settle
     * behaviour whatsoever: the last commanded step pulse and the last Z count
     * arrived on the same tick. Two things in fw/todo.md were blocked on that.
     * ELS_SLIP_SETTLE_TICKS (the horizon over which post-pulse Z motion is
     * still credited to the servo) could not be exercised here at all, because
     * every horizon behaves identically against a drivetrain that never lags.
     * And the take-up confirmation gate asks "did the carriage move far
     * enough?" but never "has it STOPPED?" — a quiescence gate that NO test
     * could make fail, because in the emulator the carriage was always already
     * stopped. This project treats a check that cannot fail as a defect in its
     * own right, so the missing model was itself the bug.
     *
     * THE SHAPE. The lash-wall push accumulates into z_settle_pending_mm rather
     * than moving the carriage, and tick() relaxes that accumulator into
     * carriage_mm as a first-order lag with time constant z_settle_tau_s. It is
     * CONSERVATIVE: every mm the drivetrain is commanded is eventually
     * delivered, just later, so no steady-state position changes and the
     * backlash model above is untouched. A first-order lag is the crudest thing
     * that has a tail at all; it is not a claim about drivetrain compliance
     * versus carriage inertia versus servo following error, which are three
     * different mechanisms that would need three different models and a
     * measurement to tell apart.
     *
     * SERVO-DRIVEN MOTION ONLY. Manual jog and move-to-position write
     * carriage_mm directly and stay instantaneous. The thing being modelled is
     * the step/dir drivetrain; making the operator's handwheel lag too would
     * perturb tests that have nothing to do with this, for no gain. It also
     * keeps the hand-nudge degree of freedom sharp: an unattributed shove is
     * supposed to be an impulse the servo had no part in.
     *
     * THE TIME CONSTANT IS NOT A MEASUREMENT. Nobody has watched Z counts
     * arrive after the last pulse of a real take-up on elspi — that is the open
     * commissioning item this model UNBLOCKS, not one it answers. The default
     * in config.cpp is chosen for its structural properties (see there), and
     * any test that depends on the settle being long or short must call
     * setSettleTauS() and say why. Do not read a number out of this model and
     * write it into Core/Src/Ramps.c. */
    double z_settle_tau_s;       /* seconds; <= 0 => instantaneous (old behaviour) */
    double z_settle_pending_mm;  /* commanded but not yet delivered to carriage_mm */

    /* Flush floor, in Z encoder counts. The lag is asymptotic and would leave
     * isCarriageSettling() true forever, which would make "has it stopped?"
     * undecidable — the exact failure this model exists to make testable. Once
     * the remainder is this far below one encoder count it cannot produce
     * another count on its own, so it is delivered in one go and the tail ends
     * at a definite tick. Small enough (1/1000 count) that the flush itself is
     * invisible in the exposed counter. */
    static constexpr double SETTLE_RESIDUAL_COUNTS = 0.001;

    /* Half-nut engagement gate: leadscrew activity is tracked from our own
     * step pulses (onStepPulse), NOT from firmware ramp internals —
     * servo.currentSpeed is the indexing/jog ramp variable and is pinned to 0
     * during pure ELS sync. */
    double time_since_step_s;  /* seconds since the last STEP pulse; large at boot */
    int    last_phys_dir;      /* physical direction of the last step pulse (+1/-1, 0 = none yet) */

    /* Drop-in position generator for stationary engagement: where within the
     * lash window the nut lands is operator-arbitrary. Fixed default seed for
     * reproducible runs; override with EMU_SEED. */
    std::minstd_rand lash_rng;

    /* Cross-slide state */
    double cross_slide_mm;

    /* Exposed (to the firmware's emulated timer-counter registers) Z/X
     * encoder counts. Ramp toward getCarriageEncoderCounts()/
     * getCrossSlideEncoderCounts() by at most MAX_EXPOSED_STEP_COUNTS per
     * tick instead of jumping straight there. Needed because
     * z_initial_mm/x_initial_mm pre-seed carriage_mm/cross_slide_mm to a
     * nonzero value before the very first tick, so the raw encoder count is
     * already large on tick 1 -- while the firmware's own DRO tracking
     * (Ramps.c's scalesDeltaPos oldPosition/position) starts at zero. A
     * single-tick exposure of that gap overflows Ramps.c:387-390's
     * `(int16_t)(position - oldPosition)` cast once the offset exceeds
     * 32767 counts (81.9175mm at 400 counts/mm) -- see els_boot_delta_test.
     * Real hardware never pre-seeds a timer counter (no
     * TIM_SetCounter/__HAL_TIM_SET_COUNTER in Core/Src), so this ramp is
     * emulator-only and invisible to any codepath hardware exercises: once
     * caught up (a handful of 100us ticks after boot), z_exposed_counts ==
     * the true count every tick, identical to the un-ramped code, and the
     * clamp (4000 counts/tick = 10mm/tick at 400 counts/mm, vs. a realistic
     * per-tick jog delta of well under 1 count at 10 mm/s / 10kHz) never
     * engages during normal motion. */
    int64_t z_exposed_counts;
    int64_t x_exposed_counts;
    static constexpr int64_t MAX_EXPOSED_STEP_COUNTS = 4000;

    /* Manual jog state (velocity-based with acceleration) */
    double z_jog_velocity;       /* current mm/s */
    double z_jog_target_dir;     /* -1, 0, or +1 (arrow key jog) */
    double x_jog_velocity;       /* current mm/s */
    double x_jog_target_dir;     /* -1, 0, or +1 (arrow key jog) */

    /* Move-to-position state */
    bool   z_move_active;
    double z_move_target;        /* target position in mm */
    bool   x_move_active;
    double x_move_target;
    double jog_max_velocity;     /* mm/s from config */
    double jog_acceleration;     /* mm/s^2 from config */
    double jog_timeout;          /* seconds since last key event */
    double z_jog_idle_timer;     /* time since last Z key */
    double x_jog_idle_timer;     /* time since last X key */
    static constexpr double JOG_KEY_TIMEOUT = 0.15; /* seconds of no key → stop */

    /* Half-nut engagement helpers */
    double getCarriageGridPhase() const;
    double snapCarriageToGrid();   /* returns signed carriage displacement (mm) */
    bool   checkPhaseAlignment() const;

    /* Engagement tolerance as a fraction of the thread grid. Distinct from
     * the (numerically similar) default z_backlash_mm and the serve-mode
     * arrival tolerance in main.cpp — three unrelated 0.02s. */
    static constexpr double PHASE_TOL_FRAC = 0.02;
    /* "Leadscrew moving" = a step pulse within this window. Pulse rate below
     * 10 Hz (≈1 RPM sync on the reference geometry) reads as stationary. */
    static constexpr double STEP_ACTIVITY_WINDOW_S = 0.1;
};

#endif /* EMU_PHYSICS_H */
