#!/bin/bash
# Fail the build if an expensive library call reappears inside the 100 kHz ISR.
#
# WHY THIS EXISTS. On 2026-08-28 elspi's ISR peak went 1012 -> 2658 cycles
# against a 1000-cycle budget -- a 2.6x overrun of the tick, which is the
# Modbus-starvation condition that lost the link on 6 of 6 cuts on 2026-08-23.
# Cause: elsReduceDeltaSpindle did its period arithmetic in DOUBLE, and
# Cortex-M4F has no FP64 hardware, so each one is a softfp library call. 22 of
# them, plus 4 __aeabi_ldivmod, inside SynchroRefreshTimerIsr.
#
# THE EMULATOR SUITE CANNOT SEE THIS AND NEVER WILL. It builds for x86, where a
# double is a hardware instruction and free. All 32 tests were green across the
# entire regression. A cost that only exists on ARM has to be checked on the ARM
# binary, which is what this does -- statically, in about a second, with no
# hardware and no cycle-accurate simulator. (A functional emulator like QEMU
# would run the calls but not model M4 timing, so it would hand back a
# confident number that is not a cycle count. Worse than this check, not
# better.)
#
# WHAT IT CANNOT CATCH: algorithmic cost in pure integer code. Nothing static
# can. The on-machine executionCyclesPeak register is ground truth for that and
# is read at flash time -- see the bench procedure.
set -u

ELF="${1:-}"
if [ -z "$ELF" ]; then
    # Default to the release build next to this script's repo.
    HERE=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
    ELF="$(dirname -- "$HERE")/build/reflex-fw.elf"
fi

OBJDUMP="${OBJDUMP:-arm-none-eabi-objdump}"
SYM="${ISR_SYM:-SynchroRefreshTimerIsr}"

if ! command -v "$OBJDUMP" >/dev/null 2>&1; then
    echo "check-isr-cost: FAIL -- $OBJDUMP not found; cannot inspect the ARM build"
    exit 2
fi
if [ ! -f "$ELF" ]; then
    echo "check-isr-cost: FAIL -- no ELF at $ELF"
    exit 2
fi

ASM=$(mktemp) || exit 2
ISR=$(mktemp) || exit 2
trap 'rm -f "$ASM" "$ISR"' EXIT

"$OBJDUMP" -d "$ELF" > "$ASM" 2>/dev/null
sed -n "/<$SYM>:/,/^\$/p" "$ASM" > "$ISR"

# ---- THE GATE THAT MATTERS MOST -----------------------------------------
# A grep for call names against a symbol that is not there finds nothing and
# reports a clean ISR. That is the degenerate-sample shape: a check structurally
# unable to fail. Refuse to pass when the symbol is missing or implausibly
# small, and say UNKNOWN rather than OK.
LINES=$(wc -l < "$ISR")
if [ "$LINES" -lt 100 ]; then
    echo "check-isr-cost: FAIL -- '$SYM' not found in $ELF, or only $LINES lines."
    echo "  Refusing to report a clean ISR from a disassembly that does not contain it."
    echo "  If the symbol was renamed or inlined away, set ISR_SYM and update this note."
    exit 2
fi

count() { grep -cE "bl[[:space:]].*<($1)>" "$ISR" || true; }

DOUBLE=$(count '__aeabi_d[a-z0-9]*|__aeabi_[a-z0-9]*2d|__[a-z]*df3')
DIVMOD=$(count '__aeabi_u?ldivmod')
LIBM=$(count 'fmodf|lroundf|powf|expf|logf|sinf|cosf|sqrt|fmod|lround')

# fmodf survives as elsFmodPitch's out-of-range fallback: unreachable on any
# real geometry (it needs a spindle advance of 2^31 pitches) but the compiler
# still emits the call. It is a branch not taken, not a cost. Raise this bound
# only with a reason written down beside it.
LIBM_BUDGET=2

RC=0
printf 'check-isr-cost: %s (%s lines)\n' "$SYM" "$LINES"
printf '  double-precision softfp calls : %-4s (budget 0)\n' "$DOUBLE"
printf '  64-bit divmod calls           : %-4s (budget 0)\n' "$DIVMOD"
printf '  libm calls                    : %-4s (budget %s)\n' "$LIBM" "$LIBM_BUDGET"

if [ "$DOUBLE" -ne 0 ]; then
    echo "  FAIL: double-precision arithmetic in the ISR. This core has no FP64"
    echo "        hardware; every one of these is a library call. Compute it off"
    echo "        the ISR and hand the ISR an integer -- see elsComputeSpindlePeriod."
    grep -oE 'bl[[:space:]].*<(__aeabi_d[a-z0-9]*|__aeabi_[a-z0-9]*2d|__[a-z]*df3)>' "$ISR" \
        | sed 's/.*<//;s/>//' | sort | uniq -c | sed 's/^/        /'
    RC=1
fi
if [ "$DIVMOD" -ne 0 ]; then
    echo "  FAIL: 64-bit division in the ISR. Cortex-M4 has a 32-bit hardware"
    echo "        divider and no 64-bit one. Use int32 where the range allows."
    RC=1
fi
if [ "$LIBM" -gt "$LIBM_BUDGET" ]; then
    echo "  FAIL: libm calls in the ISR exceed the budget of $LIBM_BUDGET."
    grep -oE 'bl[[:space:]].*<(fmodf|lroundf|powf|expf|logf|sinf|cosf|sqrt|fmod|lround)>' "$ISR" \
        | sed 's/.*<//;s/>//' | sort | uniq -c | sed 's/^/        /'
    RC=1
fi

[ "$RC" -eq 0 ] && echo "  OK"
exit "$RC"
