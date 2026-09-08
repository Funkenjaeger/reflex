#!/usr/bin/env bash
# Regression test for scripts/lib/sector0.sh -- the guard that stops
# scripts/flash.sh from overwriting the field bootloader.
#
# THE POINT OF THIS FILE IS THAT THE GUARD CAN BE SEEN TO GO RED. The thing it
# protects is a board on a lathe, and the failure it prevents is one nobody
# wants to reproduce deliberately: proving the guard by flashing a bootloaded
# board and watching it die costs a bootloader every time you want the
# assurance. So the scan takes a FILE, and this test feeds it dumps that stand
# in for both answers -- a sector 0 with the bootloader in it and a sector 0
# without -- and asserts it refuses one and passes the other. No board, no
# ST-Link, no openocd.
#
# Run under the same shell options as the callers, per lib/diag-test.sh.
#
#   bash scripts/lib/sector0-test.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=sector0.sh
. "$REPO/scripts/lib/sector0.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0

# --- dump fabrication -------------------------------------------------------

# n bytes of 0xff -- an erased sector, and the padding after a short image.
erased() { head -c "$1" /dev/zero | tr '\000' '\377'; }

BL_SIG_BYTES='\x4c\x45\x01\x00\x01\x00'   # stage 1, bootloader
APP_SIG_BYTES='\x4c\x45\x02\x00\x01\x00'  # stage 2, application

# A 16 KB sector-0 dump with `sig` planted at byte `off`, 0xff either side.
plant() {
    local out="$1" off="$2" sig="$3"
    { erased "$off"
      printf "$sig"
      erased $(( SECTOR0_SIZE - off - 6 ))
    } > "$out"
}

check() {  # check <expect: yes|no> <file> <description>
    local expect="$1" file="$2" what="$3" got
    if sector0_has_bootloader "$file"; then got=yes; else got=no; fi
    if [ "$got" = "$expect" ]; then
        echo "ok   $what -> $got"
    else
        echo "FAIL $what -> $got (expected $expect)"
        fail=1
    fi
}

# --- the two answers the guard has to tell apart ----------------------------

# 1. BOOTLOADER PRESENT. Planted at 0x1478, where it actually sits in the
#    bootloader built on 2026-09-07 -- well past the 5 KB of code, and with
#    ~11 KB of 0xff after it. That trailing run is the reason sector0_scan_hex
#    passes -v to od: without it od collapses the run to '*' and the hex string
#    stops being a faithful rendering of the dump.
plant "$TMP/bootloader.bin" $((0x1478)) "$BL_SIG_BYTES"
check yes "$TMP/bootloader.bin" "sector 0 carrying the bootloader identity window"

# 2. BOOTLOADER ABSENT, erased sector -- a virgin board.
erased "$SECTOR0_SIZE" > "$TMP/erased.bin"
check no "$TMP/erased.bin" "erased sector 0 (virgin board)"

# --- the ways it could be wrong ---------------------------------------------

# 3. The APPLICATION identity window must not trip it. Same magic, stage 2.
#    Matching on the magic alone would refuse to flash a legacy board, which
#    is the one job flash.sh has left.
plant "$TMP/appwindow.bin" $((0x0800)) "$APP_SIG_BYTES"
check no "$TMP/appwindow.bin" "sector 0 carrying the APPLICATION identity window"

# 4. Signature at offset 0, and 5. at the very end of the sector -- a scan that
#    skipped either edge would be a false negative, the dangerous direction.
plant "$TMP/at-start.bin" 0 "$BL_SIG_BYTES"
check yes "$TMP/at-start.bin" "signature at the first byte of the sector"
plant "$TMP/at-end.bin" $(( SECTOR0_SIZE - 6 )) "$BL_SIG_BYTES"
check yes "$TMP/at-end.bin" "signature at the last six bytes of the sector"

# 6. Truncated signature: the magic and stage but not the window version.
#    Must not match -- this is what a two-byte-shorter signature would accept.
{ erased 64; printf '\x4c\x45\x01\x00'; erased $(( SECTOR0_SIZE - 68 )); } \
    > "$TMP/truncated.bin"
check no "$TMP/truncated.bin" "magic + stage but no window version"

# 7. An unreadable dump is neither answer. The callers treat a failed read as
#    a refusal; what must not happen is it quietly reading as "absent".
if sector0_has_bootloader "$TMP/does-not-exist.bin"; then
    echo "FAIL missing dump reported the bootloader as present"; fail=1
elif [ "$?" = 2 ]; then
    echo "ok   missing dump -> unreadable (2), not a clean 'absent'"
else
    echo "FAIL missing dump returned $? rather than 2"; fail=1
fi

# 8. The hex rendering is COMPLETE, not merely searchable. Two hex characters
#    per byte and nothing else in the string. This is the assertion that goes
#    red if od loses -v: an all-0xff dump is one long run of identical output
#    lines, so od collapses it to a first line, a '*' and a last line, and the
#    length falls off a cliff. The substring search alone cannot see that --
#    checked by mutation on 2026-09-08, removing -v left all the checks above
#    green -- and a guard whose stated reason for a flag has no test behind it
#    is a guard whose next editor deletes the flag.
erased "$SECTOR0_SIZE" > "$TMP/allff.bin"
hexlen=$(sector0_scan_hex "$TMP/allff.bin" | wc -c)
want=$(( SECTOR0_SIZE * 2 ))
if [ "$hexlen" = "$want" ]; then
    echo "ok   hex rendering is complete ($hexlen chars for $SECTOR0_SIZE bytes)"
else
    echo "FAIL hex rendering is $hexlen chars, expected $want -- run-collapsed?"
    fail=1
fi

# --- against the real artifacts, when they are on disk ----------------------
#
# The synthetic dumps above prove the LOGIC. These prove the SIGNATURE is the
# one the toolchain actually emits, which is the half a hand-written test
# cannot assert about itself. Skipped rather than failed when the trees are not
# built: this test has to be runnable in a fresh checkout.

real_check() {  # real_check <expect> <path> <what>
    local expect="$1" path="$2" what="$3"
    if [ ! -f "$path" ]; then
        echo "skip $what ($path not built)"
        return
    fi
    head -c "$SECTOR0_SIZE" "$path" > "$TMP/real.bin"
    check "$expect" "$TMP/real.bin" "$what"
}

real_check yes "$REPO/bootloader/build/reflex-bl.bin" \
    "real bootloader .bin, first 16 KB"
real_check no  "$REPO/build/reflex-fw.bin" \
    "real legacy app .bin, first 16 KB"
real_check no  "$REPO/build-slot/reflex-fw.bin" \
    "real slotted app .bin, first 16 KB"

exit "$fail"
