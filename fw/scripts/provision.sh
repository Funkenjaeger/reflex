#!/usr/bin/env bash
# Provision a controller board: field bootloader + slotted application, over
# SWD, in one command. THIS IS THE SCRIPT FOR A NEW BOARD.
#
#   ./scripts/provision.sh              build both, program both, locally
#   ./scripts/provision.sh --no-build   program what is already built
#   ./scripts/provision.sh --dry-run    everything except the writes
#   ./scripts/provision.sh --host NAME  build here, program on NAME over ssh
#
# WHAT IT LEAVES ON THE BOARD (fw/bootloader/README.md has the geometry):
#
#   sector 0  0x08000000   16 KB  the bootloader
#   sector 1  0x08004000   16 KB  copy-state journal, ERASED = IDLE
#   sector 5  0x08020000  128 KB  RUN     -- the application, with its header
#   sector 6  0x08040000  128 KB  STAGING -- erased
#   sector 7  0x08060000  128 KB  BACKUP  -- erased
#
# WHY THIS EXISTS AT ALL. Until 2026-09-08 the only flashing script in the
# repo was scripts/flash.sh, which writes the LEGACY layout -- the application
# at 0x08000000, no bootloader. docs/setup/installing.md step 6 pointed at it,
# so a board built by the book came out with no bootloader and no way to
# discover that it should have had one. The bootloader is not an optional
# extra: it is what makes an application update possible over the RS-485 link
# the UI already holds, with no programmer and no power cycle.
#
# ONCE THIS HAS RUN, YOU DO NOT NEED IT AGAIN. Further application updates go
# over the wire:
#
#   python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/serial0
#
# (on the Pi, with reflex-ui stopped -- it holds that port -- or from the
# touchscreen's Setup -> Update, which does the same thing)
#
# This script is for a virgin board, for a board being converted from the
# legacy layout, and for recovering one whose sector 0 has been damaged.
#
# IT IS SAFE TO RE-RUN. Every write is a program-and-verify of a known image,
# and the erases only touch the journal and the two spare slots. It does NOT
# preserve anything already in RUN -- the application it programs is the one
# it just built.
#
# IT DOES NOT WRITE-PROTECT SECTOR 0. That is deliberate and it is a separate,
# considered act: WRP is what stops a later flash.sh from eating the
# bootloader, but it also has to be cleared before sector 0 can ever be
# rewritten, and getting that wrong on a board with no ROM bootloader is the
# one way to need a new board. fw/bootloader/README.md step 9b, after the
# board is confirmed working.
# END-HELP
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# shellcheck source=lib/sector0.sh
. "$REPO/scripts/lib/sector0.sh"

HOST=""          # empty = program on this machine
DO_BUILD=1
DRY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --no-build) DO_BUILD=0 ;;
        --host)     HOST="${2:?--host needs a value}"; shift ;;
        --dry-run)  DRY=1 ;;
        -h|--help)
            sed -n '2,/^# END-HELP/{/^# END-HELP/!p}' "${BASH_SOURCE[0]}" \
              | sed 's/^# \?//'
            exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

BL_DIR=bootloader/build
APP_DIR=build-slot
BL_ELF="$BL_DIR/reflex-bl.elf"
APP_BIN="$APP_DIR/reflex-fw.bin"

# THE ADDRESS COMES FROM THE HEADER, not from a constant retyped here -- the
# same reasoning as lib/diag.sh reading the probe registry out of Ramps.h. The
# RUN slot base is the one address the application is linked at; a copy in
# shell that drifted from els_identity.h would build an image for one address
# and program it at another, and the bootloader would reject the result with
# ELS_BL_ERR_VECTORS long after the mistake was made.
RUN_SLOT_BASE="$(sed -n 's/^#define[[:space:]]\+ELS_RUN_SLOT_BASE[[:space:]]\+\(0x[0-9A-Fa-f]\+\)u\?.*/\1/p' Core/Inc/els_identity.h)"
[ -n "$RUN_SLOT_BASE" ] || {
    echo "cannot read ELS_RUN_SLOT_BASE from Core/Inc/els_identity.h" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# BUILD. Two separate CMake projects; the commands are the ones in
# fw/bootloader/README.md and in .github/workflows/release.yml, so a board
# provisioned from a checkout and a board provisioned from a release asset get
# the same bytes.
# ---------------------------------------------------------------------------
if [ "$DO_BUILD" = 1 ]; then
    echo "building the bootloader (MinSizeRel; the link fails if it outgrows 16 KB)"
    cmake -S bootloader -B "$BL_DIR" -DCMAKE_BUILD_TYPE=MinSizeRel >/dev/null
    cmake --build "$BL_DIR" >/dev/null
    echo "building the slotted application (REFLEX_APP_BASE=$RUN_SLOT_BASE)"
    cmake -S . -B "$APP_DIR" -DCMAKE_BUILD_TYPE=Release \
        -DREFLEX_APP_BASE="$RUN_SLOT_BASE" >/dev/null
    cmake --build "$APP_DIR" >/dev/null
    echo
fi

[ -f "$BL_ELF" ]  || { echo "no bootloader at $BL_ELF (drop --no-build?)" >&2; exit 1; }
[ -f "$APP_BIN" ] || { echo "no application at $APP_BIN (drop --no-build?)" >&2; exit 1; }

# ---------------------------------------------------------------------------
# GATE ON THE BYTES, not on the build log. reflex_image.py patches the image
# length and CRC32 into the .bin as a POST_BUILD step that only the slotted
# CMake configuration attaches. If that wiring comes loose the build still
# succeeds and still writes a .bin -- an unheadered one, which the bootloader
# rejects as ELS_BL_ERR_HDR_MAGIC and which therefore boots into nothing. The
# same check release.yml runs on the artifacts it publishes.
#
# `info` exits 1 on any invalid header, so this is a check that can go red:
# it does on fw/build/reflex-fw.bin, the legacy image, which carries no header
# at all.
# ---------------------------------------------------------------------------
echo "validating the application image header"
if ! python3 scripts/reflex_image.py info "$APP_BIN"; then
    cat >&2 <<EOF

REFUSING TO PROGRAM: $APP_BIN has no valid RFLX image header.

The bootloader validates magic, header version, length and CRC32 before it
will jump, so an unheadered image in RUN is a board that boots into the
bootloader and stays there. Two ways to get here:

  * the wrong file -- fw/build/reflex-fw.bin is the LEGACY image and has no
    header by design. This script wants $APP_BIN.
  * the post-build patch did not run. Rebuild without --no-build; if it still
    fails, check the 'if (REFLEX_SLOTTED)' block in fw/CMakeLists.txt.
EOF
    exit 1
fi
echo

REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DIRTY=false
git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || DIRTY=true
if [ "$DIRTY" = true ]; then
    echo "WARNING: working tree is dirty. '$REV' does not fully identify these"
    echo "         binaries; recording them as ${REV}-dirty. The identity window"
    echo "         will report the dirty flag too."
    echo
fi

BL_MD5="$(md5sum "$BL_ELF" | cut -d' ' -f1)"
APP_MD5="$(md5sum "$APP_BIN" | cut -d' ' -f1)"

if [ -n "$HOST" ]; then
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
    ssh "$HOST" 'mkdir -p ~/firmware'
    TARGET_BL="firmware/reflex-bl.elf"
    TARGET_APP="firmware/reflex-app.bin"
    scp -q "$BL_ELF" "$HOST:$TARGET_BL"
    scp -q "$APP_BIN" "$HOST:$TARGET_APP"
    # Same reasoning as flash.sh: a transfer that can silently truncate is
    # worth verifying before it is written to a machine with moving parts.
    for pair in "$TARGET_BL:$BL_MD5" "$TARGET_APP:$APP_MD5"; do
        remote_path="${pair%:*}"; want="${pair##*:}"
        got="$(ssh "$HOST" "md5sum $remote_path | cut -d' ' -f1")"
        [ "$want" = "$got" ] || {
            echo "TRANSFER CORRUPT: $remote_path local $want != remote $got -- not programming" >&2
            exit 1
        }
    done
    RUN=(ssh "$HOST")
    WHERE="$HOST"
else
    TARGET_BL="$REPO/$BL_ELF"
    TARGET_APP="$REPO/$APP_BIN"
    RUN=(bash -c)
    WHERE="this machine"
    mkdir -p ~/firmware
fi

OCD="openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c 'transport select swd'"

# ---------------------------------------------------------------------------
# PREFLIGHT: prove the probe can reach the target BEFORE the first erase.
#
# The erase is the first irreversible step, and openocd failing halfway
# through the sequence leaves a board with an erased journal and no
# bootloader, which is a worse state than the one it started in. So the first
# thing that happens is a read.
#
# It is only a reachability check. Unlike flash.sh's preflight it does not care
# what it finds: programming a bootloader over an existing bootloader, or over
# a legacy application, is exactly what this script is for.
# ---------------------------------------------------------------------------
# 'reset run' after the read, for the reason flash.sh's preflight has one:
# openocd exiting does not resume a halted core, and on --dry-run or an abort
# below there is no later write step to reset it.
PROBE_CMD="$OCD -c 'init; reset halt; flash info 0; reset run; shutdown'"
echo "checking the ST-Link can reach the target from ${WHERE}"
PROBE_OUT="$(mktemp)"
trap 'rm -f "$PROBE_OUT"' EXIT
if ! "${RUN[@]}" "$PROBE_CMD" >"$PROBE_OUT" 2>&1; then
    cat "$PROBE_OUT" >&2
    cat >&2 <<EOF

CANNOT REACH THE TARGET from ${WHERE}. Nothing has been written.

Check in this order: openocd installed; the ST-Link plugged into this machine
and into the board's SWD header; the board powered; and the 'plugdev' udev
rules in place (they come with openocd's packaging, and a fresh login may be
needed for the group to take effect).
EOF
    exit 1
fi

# WRP on sector 0 is a legitimate state -- a commissioned board has it -- and
# it makes the bootloader write below fail. Say so now rather than after the
# erase. Not fatal: a wording this does not recognize must not block
# provisioning an unprotected board. The match itself is in lib/sector0.sh and
# tested there; the grep it replaced also matched "not protected", so this
# warning fired on every board and meant nothing.
if sector0_wrp_reported "$PROBE_OUT"; then
    cat <<EOF

NOTE: openocd reports write protection on flash bank 0. If that covers sector
0, programming the bootloader will fail. Clear it and power-cycle first:

  $OCD -c 'init; reset halt; flash protect 0 0 0 off; shutdown'

(fw/bootloader/README.md step 9b. RDP stays at level 0 throughout.)
EOF
fi
echo

# ---------------------------------------------------------------------------
# THE WRITES, in the order fw/bootloader/README.md steps 4 and 5 establish.
#
# Erase first, and erase MORE than strictly necessary: the journal (sector 1)
# and both spare slots (6, 7). An erased journal reads as IDLE, so the
# bootloader starts from a known copy-state instead of resuming a copy left
# behind by whatever was on the board before. Stale bytes in STAGING and
# BACKUP are not dangerous -- the bootloader validates a header and CRC before
# it uses either -- but a board that has just been provisioned should not have
# a plausible-looking image in a slot nobody put there.
#
# The bootloader goes in as an ELF (it carries its own load address); the
# application goes in as the .bin at the RUN slot base, because its ELF still
# holds the zero length/CRC placeholders that reflex_image.py patched into the
# .bin. Programming the app ELF would write an image the bootloader rejects.
# ---------------------------------------------------------------------------
ERASE_CMD="$OCD -c 'init; reset halt; flash erase_sector 0 1 1; flash erase_sector 0 6 7; shutdown'"
BL_CMD="$OCD -c 'program $TARGET_BL verify exit'"
APP_CMD="$OCD -c 'program $TARGET_APP $RUN_SLOT_BASE verify reset exit'"

if [ "$DRY" = 1 ]; then
    cat <<EOF
DRY RUN -- the build, the header validation and the reachability check above
all actually happened. Stopping before the writes. Would now run on ${WHERE}:

  $ERASE_CMD

  $BL_CMD

  $APP_CMD
EOF
    exit 0
fi

echo "erasing the journal (sector 1) and the spare slots (sectors 6-7)"
"${RUN[@]}" "$ERASE_CMD"
echo
echo "programming the bootloader into sector 0"
"${RUN[@]}" "$BL_CMD"
echo
echo "programming the application into RUN (${RUN_SLOT_BASE})"
"${RUN[@]}" "$APP_CMD"

# Same manifest flash.sh writes, and deliberately the same file: "what is on
# this board" has to be answerable from one place regardless of which script
# put it there. `variant` says provision so the two are distinguishable.
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MANIFEST="{\"utc\":\"$STAMP\",\"variant\":\"provision\",\"probe\":null,\"rev\":\"$REV\",\"dirty\":$DIRTY,\"md5\":\"$APP_MD5\",\"blMd5\":\"$BL_MD5\"}"
"${RUN[@]}" "printf '%s\n' '$MANIFEST' >> ~/firmware/flashed.json"

echo
echo "written  BOOTLOADER + APPLICATION  rev ${REV}$([ "$DIRTY" = true ] && echo " (dirty)")"
echo "  recorded in ${WHERE}:~/firmware/flashed.json"
cat <<'EOF'

  ############################################################
  #  NOW POWER-CYCLE THE CONTROLLER. THIS IS NOT OPTIONAL.   #
  ############################################################

  openocd's "Verified OK" confirms the FLASH CONTENTS match the image. It
  says nothing about what the core is EXECUTING, and on this board a reset
  alone does not reliably start the new firmware -- observed 2026-08-16.

  A power cycle is also the only thing that exercises the real VBAT-less
  path: this board has no VBAT, so the RTC backup registers holding the
  boot-attempt counter zero on power loss, and that is the state the
  bootloader will actually meet in the field.

  Then confirm BOTH stages, with the UI stopped:

    python3 scripts/modbus-flash.py --identity --port /dev/serial0

  Expect stage=application and the rev this script just built. If it says
  stage=bootloader instead, the bootloader is alive but did not accept the
  image in RUN -- read blStatus and blRunValid from the same output before
  reprogramming anything.

  Then start the UI and check its log:

    Firmware register protocol version N (expected N)

  Write-protecting sector 0 is a separate step, after the board is confirmed
  working: fw/bootloader/README.md step 9b.
EOF
