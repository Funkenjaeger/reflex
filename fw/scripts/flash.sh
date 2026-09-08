#!/usr/bin/env bash
# ############################################################################
# #  THIS IS THE LEGACY / RECOVERY TOOL. IT IS NOT HOW YOU SET UP A BOARD.   #
# ############################################################################
#
# It writes the LEGACY layout: the application linked at 0x08000000, no image
# header, no bootloader anywhere. That was the only layout there was until
# 2026-09-07. It is not the layout a board should be built with now.
#
#   NEW BOARD, or a board that should have the field bootloader:
#       ./scripts/provision.sh          bootloader + slotted app, both slots
#
#   EXISTING BOARD that already has the bootloader, new application:
#       python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port ...
#       -- over the RS-485 link, no programmer, no power cycle
#
# This script remains for exactly two cases: a board still on the legacy
# layout, and deliberately taking a board back to it. On a board carrying the
# bootloader it would overwrite sector 0 with the application's vector table
# and destroy it, so it now READS SECTOR 0 FIRST AND REFUSES -- see
# --force-legacy below, and lib/sector0.sh for how it decides.
#
# Build and flash the LEGACY firmware, in one command.
#
#   ./scripts/flash.sh              release build, flash locally
#   ./scripts/flash.sh --diag=NAME  diagnostic build carrying ONE probe (DIAG.md)
#   ./scripts/flash.sh --no-build   flash what is already built
#   ./scripts/flash.sh --dry-run    everything except the write
#   ./scripts/flash.sh --host NAME  build here, flash on NAME over ssh
#   ./scripts/flash.sh --force-legacy
#                                   program the legacy layout even though the
#                                   board is carrying the bootloader. DESTROYS
#                                   IT. Clear its write protection first
#                                   (bootloader/README.md step 9b, 'off') or
#                                   openocd fails on sector 0 regardless.
#
# LOCAL IS THE DEFAULT, and that is the whole point. Run this on the machine
# with the ST-Link plugged in -- which for this project is the Pi that also runs
# the UI -- and there is no copy, no second machine, and no way for the binary on
# the target to be a different revision from the checkout you are looking at.
# `git rev-parse HEAD` there IS what is flashed.
#
# --host exists for the case where the probe host genuinely cannot build (no ARM
# toolchain, or you want the faster machine to compile). It adds a copy and a
# checksum, because a transfer that can silently truncate is worth verifying
# before it gets written to the controller of a machine with moving parts.
#
# EITHER WAY IT REBUILDS FIRST. --no-build opts out. A stale binary is the
# easiest mistake to make and the hardest to notice, and rebuilding costs
# seconds.
#
# IT RECORDS WHAT IT FLASHED, in ~/firmware/flashed.json on the probe host.
# Working out what firmware was on this lathe once took an afternoon of
# archaeology across build-artifact timestamps on a machine that was powered
# off. One line of JSON per flash makes that a lookup. Nothing reads it yet; it
# exists so the question has an answer.
# END-HELP
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# shellcheck source=lib/diag.sh
. "$REPO/scripts/lib/diag.sh"
# shellcheck source=lib/sector0.sh
. "$REPO/scripts/lib/sector0.sh"

VARIANT=release
BUILD_DIR=build
PROBE=""
HOST=""          # empty = flash on this machine
DO_BUILD=1
DRY=0
FORCE_LEGACY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --diag)
            echo "--diag requires a probe: --diag=<name>" >&2
            diag_usage_probes "$REPO"
            exit 2 ;;
        --diag=*)
            PROBE="${1#--diag=}"
            VARIANT=diagnostic
            BUILD_DIR="$(diag_build_dir "$PROBE")"
            # Validated here as well as in build.sh, because --no-build skips
            # build.sh entirely. Without this, `--diag=typo --no-build` would
            # sail past every check and flash whatever happened to be sitting in
            # a directory named after the typo -- or fail with a confusing
            # "no such ELF" instead of "no such probe".
            if ! diag_resolve "$REPO" "$PROBE" >/dev/null; then
                echo "unknown diagnostic probe: $PROBE" >&2
                diag_usage_probes "$REPO"
                exit 2
            fi ;;
        --no-build) DO_BUILD=0 ;;
        --host)     HOST="${2:?--host needs a value}"; shift ;;
        --dry-run)  DRY=1 ;;
        --force-legacy) FORCE_LEGACY=1 ;;
        -h|--help)
            # Sentinel rather than a line number. The old '2,29p' was correct
            # only until the header changed length, and a --help that silently
            # stops mid-sentence is how a warning stops being read.
            sed -n '2,/^# END-HELP/{/^# END-HELP/!p}' "${BASH_SOURCE[0]}" \
              | sed 's/^# \?//'
            diag_usage_probes "$REPO"
            exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "$DO_BUILD" = 1 ]; then
    # NOT `test && a || b` -- if the diagnostic build FAILED, that idiom falls
    # through and quietly builds release instead, which is the precise
    # wrong-variant confusion this script exists to prevent.
    if [ "$VARIANT" = diagnostic ]; then
        "$REPO/scripts/build.sh" "--diag=$PROBE"
    else
        "$REPO/scripts/build.sh"
    fi
    echo
fi

ELF="$BUILD_DIR/reflex-fw.elf"
[ -f "$ELF" ] || { echo "no firmware at $ELF (drop --no-build?)" >&2; exit 1; }

REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DIRTY=false
git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || DIRTY=true

# A dirty tree WARNS rather than blocks. Refusing would just get worked around,
# and iterating at the machine is legitimate -- but the revision alone then stops
# identifying the binary, so the manifest records it.
if [ "$DIRTY" = true ]; then
    echo "WARNING: working tree is dirty. '$REV' does not fully identify this"
    echo "         binary; recording it as ${REV}-dirty."
    echo
fi

MD5="$(md5sum "$ELF" | cut -d' ' -f1)"

if [ -n "$HOST" ]; then
    # Preflight before doing anything that looks like progress. Note ssh config
    # is per-context: a host alias that works in a Windows terminal does not
    # exist inside WSL, which has its own ~/.ssh.
    if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$HOST" true 2>/dev/null; then
        cat >&2 <<EOF
cannot reach '$HOST' over ssh from this machine.

Needs passwordless ssh to the machine with the ST-Link attached: the host must
resolve here, and your key must be authorized there in THIS shell's ssh context.

Simpler option: install the ARM toolchain on the probe host and run this script
there with no --host at all.
EOF
        exit 1
    fi
    TARGET_ELF="firmware/reflex-fw-${VARIANT}.elf"
    RUN=(ssh "$HOST")
    WHERE="$HOST"
else
    TARGET_ELF="$REPO/$ELF"
    RUN=(bash -c)
    WHERE="this machine"
fi

# ---------------------------------------------------------------------------
# SECTOR-0 PREFLIGHT. Read what is on the board before writing over it.
#
# This is a READ, so it runs on --dry-run too: a dry run that skipped it would
# report "would now program ..." for a board this script must not touch, which
# is worse than not offering the preview at all.
#
# FAIL-CLOSED. A dump we could not take is not evidence of an empty sector 0.
# openocd not installed, no probe, target held in reset, WRP on sector 0 -- all
# land here, and all of them mean "unknown", which this treats as "do not
# write". --force-legacy is the way past it, and it is the same flag as for a
# confirmed bootloader on purpose: both are the operator saying they know what
# is on the board and this script does not.
# ---------------------------------------------------------------------------
DUMP_LOCAL="$(mktemp)"
trap 'rm -f "$DUMP_LOCAL"' EXIT

if [ "$FORCE_LEGACY" = 1 ]; then
    echo "--force-legacy: skipping the sector-0 preflight."
    echo "  If this board has the field bootloader, this WILL destroy it."
    echo
else
    DUMP_REMOTE="firmware/sector0-preflight.bin"
    if [ -n "$HOST" ]; then
        ssh "$HOST" 'mkdir -p ~/firmware'
        DUMP_PATH="$DUMP_REMOTE"
    else
        DUMP_PATH="$DUMP_LOCAL"
    fi

    # 'reset halt' because dump_image needs the core stopped; the write step
    # below issues its own 'reset' and leaves the target running, so a halt
    # here is not left behind.
    DUMP_CMD="openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
    -c 'transport select swd' \
    -c 'init; reset halt; dump_image $DUMP_PATH $SECTOR0_BASE $SECTOR0_SIZE; shutdown'"

    echo "reading sector 0 (${SECTOR0_BASE}, ${SECTOR0_SIZE} bytes) from the board on ${WHERE}"
    DUMP_OK=1
    "${RUN[@]}" "$DUMP_CMD" >/dev/null 2>&1 || DUMP_OK=0
    if [ "$DUMP_OK" = 1 ] && [ -n "$HOST" ]; then
        scp -q "$HOST:$DUMP_REMOTE" "$DUMP_LOCAL" || DUMP_OK=0
    fi
    # A dump of the wrong length is as unknown as no dump at all.
    if [ "$DUMP_OK" = 1 ]; then
        [ "$(stat -c %s "$DUMP_LOCAL" 2>/dev/null || echo 0)" = "$SECTOR0_SIZE" ] || DUMP_OK=0
    fi

    if [ "$DUMP_OK" != 1 ]; then
        cat >&2 <<EOF

REFUSING TO FLASH: could not read sector 0 from the board on ${WHERE}.

Every reason for that -- no openocd, no ST-Link, the target held in reset, or
sector 0 write-protected -- leaves this script unable to tell whether the field
bootloader is on this board. It will not write the legacy layout over an
unknown sector 0.

Re-run the read on its own to see openocd's own message:

  $DUMP_CMD

If you know this board is on the legacy layout, or you mean to take it back
there and have already cleared the bootloader's write protection:

  ./scripts/flash.sh --force-legacy
EOF
        exit 1
    fi

    if sector0_has_bootloader "$DUMP_LOCAL"; then
        echo >&2
        sector0_refuse_message --force-legacy >&2
        exit 1
    fi
    echo "  no bootloader in sector 0 -- legacy layout, safe to program"
    echo
fi

echo "flashing ${VARIANT^^} (${REV}$([ "$DIRTY" = true ] && echo -dirty)) on ${WHERE}"

if [ -n "$HOST" ]; then
    ssh "$HOST" 'mkdir -p ~/firmware'
    scp -q "$ELF" "$HOST:$TARGET_ELF"
    REMOTE_MD5="$(ssh "$HOST" "md5sum $TARGET_ELF | cut -d' ' -f1")"
    [ "$MD5" = "$REMOTE_MD5" ] || {
        echo "TRANSFER CORRUPT: local $MD5 != remote $REMOTE_MD5 -- not flashing" >&2
        exit 1
    }
else
    mkdir -p ~/firmware
fi

# 'transport select swd' is explicit to suppress openocd's auto-select
# deprecation warning. 'reset' leaves the target executing rather than halted --
# a halted lathe controller with the UI still polling it looks like a comms
# fault and invites debugging the wrong thing.
OPENOCD_CMD="openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
    -c 'transport select swd' \
    -c 'program $TARGET_ELF verify reset exit'"

if [ "$DRY" = 1 ]; then
    echo
    echo "DRY RUN -- everything above actually happened. Stopping before the"
    echo "write. Would now run on ${WHERE}:"
    echo
    echo "  $OPENOCD_CMD"
    exit 0
fi

"${RUN[@]}" "$OPENOCD_CMD"

STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# `probe` is recorded alongside `variant` because "diagnostic" alone stopped
# being a complete answer once there could be more than one probe: the schema
# decides what every field in the scratchpad means, so a manifest that omits it
# cannot tell you what a capture you pulled last week was measuring. null for
# release builds -- an absent probe, stated, rather than a missing key.
MANIFEST="{\"utc\":\"$STAMP\",\"variant\":\"$VARIANT\",\"probe\":$([ -n "$PROBE" ] && printf '"%s"' "$PROBE" || printf 'null'),\"rev\":\"$REV\",\"dirty\":$DIRTY,\"md5\":\"$MD5\"}"
"${RUN[@]}" "printf '%s\n' '$MANIFEST' >> ~/firmware/flashed.json"

echo
echo "written  ${VARIANT^^}  rev ${REV}$([ "$DIRTY" = true ] && echo " (dirty)")"
echo "  recorded in ${WHERE}:~/firmware/flashed.json"
cat <<'EOF'

  ############################################################
  #  NOW POWER-CYCLE THE CONTROLLER. THIS IS NOT OPTIONAL.   #
  ############################################################

  openocd's "Verified OK" confirms the FLASH CONTENTS match the image. It
  says nothing about what the core is EXECUTING, and on this board a reset
  alone does not reliably start the new firmware -- observed 2026-08-16.

  The failure is silent and convincing: programming and verification both
  report success, the UI reconnects, and the machine goes on running the
  PREVIOUS firmware with no error anywhere. That cost an hour of chasing a
  build bug that did not exist.

  After the power cycle, confirm from the reflex-ui log that the firmware
  you flashed is the firmware that is running:

    Firmware register protocol version N (expected N)
EOF
if [ "$VARIANT" = diagnostic ]; then
    cat <<'EOF'
    ELS diagnostic recorder active: schema=...

  'dormant' or a protocol mismatch means the new image is not running --
  power-cycle again rather than assuming it took.
EOF
fi
