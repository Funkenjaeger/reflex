# What is at 0x08000000, and may the legacy layout be written over it?
# Sourced by scripts/flash.sh and scripts/provision.sh; not executable.
#
# THE PROBLEM. Two different things are legitimately programmed at 0x08000000
# on this board and only one of them can be there at a time:
#
#   the field bootloader   16 KB, sector 0, and the application lives in the
#                          RUN slot at 0x08020000 behind it
#   the legacy application linked at 0x08000000, ~41 KB, spilling through
#                          sectors 0-3, with no bootloader anywhere
#
# `scripts/flash.sh` writes the second one. Run against a board carrying the
# first, it overwrites sector 0 with the application's vector table and the
# bootloader is gone -- silently, because openocd reports "Verified OK" for
# exactly what it was asked to write. The board still runs; it has simply lost
# the ability to be flashed over the wire, and the only way back is the ST-Link
# that was just used to destroy it.
#
# THE DECISION lives in lib/sector0.py, which classifies a dump of the whole
# legacy region (0x08000000 up to the RUN slot, 128 KB) as LEGACY, ERASED,
# BOOTLOADER or UNKNOWN, and flash.sh proceeds on the first two ONLY. That is
# an ALLOWLIST: until 2026-09-11 this file grepped sector 0 for one six-byte
# bootloader signature and proceeded when it was absent, which failed dangerous
# on a window-version bump, on any sector-0 content that was not exactly that
# bootloader, and on a stale dump. sector0.py's docstring has the rules and the
# one residual case they cannot see.
#
# WHY A FILE. The classifier takes a FILE, not a target, so it can be exercised
# against fabricated and real-binary dumps with no board, no ST-Link and no
# openocd in the room -- lib/sector0-test.sh. A guard that has only ever been
# run against hardware nobody dares reproduce the failure on is a guard nobody
# knows the state of.

SECTOR0_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sector0.py"

# Exit codes of `sector0.py classify`, named once. Callers must treat any
# status NOT listed as proceed-able as a refusal -- 127 (no python3) included.
SECTOR0_LEGACY=0
SECTOR0_ERASED=10
SECTOR0_BOOTLOADER=20

# Set SECTOR0_REGION_BASE / SECTOR0_REGION_SIZE from the firmware headers, the
# same values the classifier checks the dump against. Returns nonzero, with the
# reason on stderr, when they cannot be derived; the caller refuses.
sector0_load_region() {
    local out
    out="$(python3 "$SECTOR0_PY" constants)" || return 1
    SECTOR0_REGION_BASE="$(printf '%s\n' "$out" | sed -n 's/^ELS_FLASH_BASE=//p')"
    SECTOR0_REGION_SIZE="$(printf '%s\n' "$out" | sed -n 's/^REGION_SIZE=//p')"
    [ -n "$SECTOR0_REGION_BASE" ] && [ -n "$SECTOR0_REGION_SIZE" ] || return 1
    # openocd takes the hex base as-is; the length check wants decimal.
    SECTOR0_REGION_SIZE=$((SECTOR0_REGION_SIZE))
}

# Classify the dump in $1. Prints the verdict and its reasons; returns the
# classifier's exit code.
sector0_classify() {
    python3 "$SECTOR0_PY" classify "$1"
}

# True if openocd's `flash info 0` output in $1 reports sector 0 protected.
#
# openocd prints each sector as `#  0: 0x00000000 (0x4000 16kB) <state>`, where
# <state> is "protected", "not protected" or "protection state unknown". A
# grep for the bare word "protected" matches the SECOND of those, so the
# warning it guarded fired on every unprotected board -- and a warning that
# always fires is one nobody reads. This matches sector 0's line only, and only
# when the state is exactly "protected".
sector0_wrp_reported() {
    grep -Eq '#[[:space:]]*0:[[:space:]].*\)[[:space:]]+protected[[:space:]]*$' "$1"
}

# The bootloader refusal, printed by both callers so the wording only exists
# once. $1 is the name of the override flag the calling script offers.
sector0_refuse_message() {
    cat <<EOF
REFUSING TO FLASH: this board is carrying the field bootloader.

Sector 0 holds the bootloader's identity window, so the application on this
board lives in the RUN slot at 0x08020000 behind it. Programming the legacy
0x08000000 image over the top would overwrite the bootloader's vector table
and destroy it. openocd would report success.

What you almost certainly want instead:

  ./scripts/provision.sh              rebuild and reprogram bootloader + app
  python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/serial0
                                      update the app over RS-485, no programmer
                                      (stop reflex-ui first; it holds the port)

If you really are taking this board BACK to the legacy no-bootloader layout --
a deliberate act, and the bootloader's write protection has to be cleared for
it to even succeed (fw/bootloader/README.md step 9b with 'off') -- then say so:

  ./scripts/flash.sh $1
EOF
}

# The refusal for everything the allowlist does not recognize. $1 is the
# override flag; $2 is the classifier's own output, which says which test the
# region failed.
sector0_unknown_message() {
    cat <<EOF
REFUSING TO FLASH: the flash at 0x08000000 is not a layout this script
recognizes, so it cannot tell whether writing over it destroys something.

It writes only over two things: the legacy application (vectors for an image
linked at 0x08000000, a Reflex application identity window, and a sector 0
filled by the image), or an erased sector 0. What it read:

$(printf '%s\n' "$2" | sed 's/^/  /')

A bootloader this project does not know, a half-written image, and a slotted
application programmed at the wrong address all land here, and none of them is
safe to assume is disposable.

If you have identified what is on this board and mean to replace it with the
legacy layout:

  ./scripts/flash.sh $1
EOF
}
