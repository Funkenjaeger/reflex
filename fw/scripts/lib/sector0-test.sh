#!/usr/bin/env bash
# Regression test for scripts/lib/sector0.py -- the classifier that decides
# whether scripts/flash.sh may write the legacy layout over a board.
#
# THE POINT OF THIS FILE IS THAT THE GUARD CAN BE SEEN TO GO RED. The thing it
# protects is a board on a lathe, and the failure it prevents is one nobody
# wants to reproduce deliberately: proving the guard by flashing a bootloaded
# board and watching it die costs a bootloader every time you want the
# assurance. So the classifier takes a FILE, and this test feeds it dumps that
# stand in for every answer -- fabricated ones for the logic, and the real
# built binaries for the signature -- with no board, no ST-Link and no openocd.
#
# SECTOR0_REQUIRE_REAL=1 turns a skipped real-binary check into a FAILURE.
# CI's arm-build job sets it after building all three images. Without it those
# checks self-skip on an unbuilt tree -- which, until 2026-09-11, was every CI
# run, so the only check tying the classifier to the bytes the toolchain
# actually emits had run exactly once, by hand.
#
# Run under the same shell options as the callers, per lib/diag-test.sh.
#
#   bash scripts/lib/sector0-test.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=sector0.sh
. "$REPO/scripts/lib/sector0.sh"
# shellcheck source=sector0-fixtures.sh
. "$REPO/scripts/lib/sector0-fixtures.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0
REQUIRE_REAL="${SECTOR0_REQUIRE_REAL:-0}"

verdict_name() {
    case "$1" in
        0) echo LEGACY ;; 10) echo ERASED ;; 20) echo BOOTLOADER ;; 21) echo UNKNOWN ;;
        30) echo UNREADABLE ;; 31) echo UNDERIVABLE ;; *) echo "exit-$1" ;;
    esac
}

check() {  # check <expect-verdict> <file> <description>
    local expect="$1" file="$2" what="$3" rc out got
    out="$(sector0_classify "$file")" && rc=0 || rc=$?
    got="$(verdict_name "$rc")"
    # The verdict word printed and the exit code must agree; a caller reads
    # the code, a human reads the word.
    if [ "$got" = "$expect" ] && { [ "$rc" -ge 30 ] || [ "${out%%$'\n'*}" = "$expect" ]; }; then
        echo "ok   $what -> $got"
    else
        echo "FAIL $what -> $got (expected $expect)"
        printf '%s\n' "$out" | sed 's/^/       | /'
        fail=1
    fi
}

fx() {  # fx <fixture-name> -> path of a freshly built fixture
    sector0_fixture "$1" "$TMP/$1.bin"
    printf '%s' "$TMP/$1.bin"
}

echo "== lib/sector0.py: the allowlist =="

# --- the constants are derived, and match the region flash.sh will dump -----
if sector0_load_region; then
    echo "ok   region derived from els_identity.h: $SECTOR0_REGION_BASE + $SECTOR0_REGION_SIZE"
    [ "$SECTOR0_REGION_SIZE" = 131072 ] && [ "$((SECTOR0_REGION_BASE))" = "$((0x08000000))" ] \
        || { echo "FAIL region is not 0x08000000 + 128 KB -- geometry moved; re-read sector0.py"; fail=1; }
else
    echo "FAIL could not derive the region from the headers"; fail=1
fi

# --- the two it proceeds on ---------------------------------------------------
check LEGACY     "$(fx legacy)"        "legacy application at 0x08000000"
check ERASED     "$(fx erased)"        "erased region (virgin board)"

# The APPLICATION window's version is not part of the identification either.
check LEGACY     "$(fx legacy-v7)"     "legacy application, window version 7"

# --- the bootloader, at any window version -------------------------------------
check BOOTLOADER "$(fx bootloader)"    "bootloader in sector 0, window version 1"
# THE REGRESSION that motivated the rewrite: the old six-byte signature
# included the version word, so a version bump made the bootloader invisible.
check BOOTLOADER "$(fx bootloader-v2)" "bootloader, window version 2"
check BOOTLOADER "$(fx bootloader-vff)" "bootloader, window version 0xFFFF"
check BOOTLOADER "$(fx bootloader-at-end)" "bootloader window in the last six bytes of sector 0"
# A provisioned board still carries the legacy app's window in its leftovers;
# the bootloader window in sector 0 must win.
check BOOTLOADER "$(fx converted)"     "bootloader over legacy leftovers (a converted board)"

# --- what the old denylist waved through ---------------------------------------
# A bootloader with no Reflex identity window, on a board whose sectors 2-3
# still hold a legacy app: plausible vectors AND an app window, so only the
# erased run in sector 0 tells it apart.
check UNKNOWN    "$(fx foreign)"       "unidentified bootloader over legacy leftovers"
check UNKNOWN    "$(fx slot-at-zero)"  "slotted application programmed at 0x08000000"
check UNKNOWN    "$(fx legacy-nowindow)" "an image with legacy vectors but no identity window"
# A window-shaped pattern at an odd offset is not a uint16_t array; it must not
# refuse a legacy board (or anything else).
check LEGACY     "$(fx legacy-oddbl)"  "bootloader-like bytes at an ODD offset in a legacy app"

# --- fail-closed ---------------------------------------------------------------
check UNREADABLE "$TMP/does-not-exist.bin" "missing dump"
sector0_fixture erased "$TMP/short.bin" 16384
check UNREADABLE "$TMP/short.bin"      "dump of the wrong length (16 KB, the old size)"

# Constants that cannot be derived refuse rather than guess. Run a copy of the
# classifier against a tree whose header lacks ELS_ID_MAGIC.
mkdir -p "$TMP/fakefw/scripts/lib" "$TMP/fakefw/Core/Inc" "$TMP/fakefw/bootloader/core"
cp "$SECTOR0_PY" "$TMP/fakefw/scripts/lib/"
grep -v 'define ELS_ID_MAGIC' "$REPO/Core/Inc/els_identity.h" > "$TMP/fakefw/Core/Inc/els_identity.h"
cp "$REPO/bootloader/core/bl_image.c" "$TMP/fakefw/bootloader/core/"
rc=0; python3 "$TMP/fakefw/scripts/lib/sector0.py" classify "$(fx legacy)" >/dev/null || rc=$?
if [ "$rc" = 31 ]; then echo "ok   underivable constants -> UNDERIVABLE (31), not a verdict"
else echo "FAIL underivable constants -> exit $rc, expected 31"; fail=1; fi

# --- provision.sh's write-protect warning ----------------------------------------
# openocd's own three wordings for a sector. The old grep matched "protected"
# inside "not protected", so the warning fired on every unprotected board.
wrp() {  # wrp <expect yes|no> <state words> <description>
    printf '#  0: 0x00000000 (0x4000 16kB) %s\n#  1: 0x00004000 (0x4000 16kB) protected\n' "$2" > "$TMP/finfo"
    if sector0_wrp_reported "$TMP/finfo"; then got=yes; else got=no; fi
    if [ "$got" = "$1" ]; then echo "ok   $3 -> $got"; else echo "FAIL $3 -> $got (expected $1)"; fail=1; fi
}
wrp yes "protected"                "flash info: sector 0 protected"
wrp no  "not protected"            "flash info: sector 0 not protected (sector 1 protected)"
wrp no  "protection state unknown" "flash info: sector 0 protection unknown"

# --- against the real artifacts ---------------------------------------------------
#
# The fixtures above prove the LOGIC. These prove it against the bytes the
# toolchain actually emits, which is the half a hand-written test cannot assert
# about itself: a changed magic, a moved window, a legacy build that grew a
# 1 KB run of 0xFF in its first sector.

real_check() {  # real_check <expect> <path> <what>
    local expect="$1" path="$2" what="$3"
    if [ ! -f "$path" ]; then
        if [ "$REQUIRE_REAL" = 1 ]; then
            echo "FAIL $what ($path not built, and SECTOR0_REQUIRE_REAL=1)"; fail=1
        else
            echo "skip $what ($path not built)"
        fi
        return
    fi
    pad_region "$path" "$TMP/real.bin"
    check "$expect" "$TMP/real.bin" "$what"
}

# The image, then 0xFF to 128 KB -- what the region reads as with this image
# programmed at 0x08000000 and nothing else. Appended rather than cut with
# `head -c`: a producer piped into an early-exiting head is a SIGPIPE, and
# under pipefail that failed this test at random (2026-09-11).
pad_region() {  # pad_region <image> <out>
    local size
    size="$(stat -c %s "$1")"
    [ "$size" -le 131072 ] || { echo "FAIL $1 is larger than the region"; fail=1; return 1; }
    cp "$1" "$2"
    s0fx_fill $(( 131072 - size )) '\377' >> "$2"
}

real_check BOOTLOADER "$REPO/bootloader/build/reflex-bl.bin" "real bootloader image"
real_check LEGACY     "$REPO/build/reflex-fw.bin"            "real legacy application image"
real_check UNKNOWN    "$REPO/build-slot/reflex-fw.bin"       "real slotted image, at the wrong address"

# The real bootloader with its window version bumped: the identification does
# not depend on the number, on the real bytes as well as the fabricated ones.
if [ -f "$REPO/bootloader/build/reflex-bl.bin" ]; then
    pad_region "$REPO/bootloader/build/reflex-bl.bin" "$TMP/blv2.bin"
    off="$(python3 - "$TMP/blv2.bin" <<'PY'
import sys
d = open(sys.argv[1], "rb").read()
i = d.find(b"\x4c\x45\x01\x00")
while i != -1 and i % 2:
    i = d.find(b"\x4c\x45\x01\x00", i + 1)
print(i)
PY
)"
    if [ "$off" -ge 0 ]; then
        s0fx_poke "$TMP/blv2.bin" $(( off + 4 )) '\x02\x00'
        check BOOTLOADER "$TMP/blv2.bin" "real bootloader image, window version patched to 2"
    else
        echo "FAIL real bootloader image carries no identity window at all"; fail=1
    fi
elif [ "$REQUIRE_REAL" = 1 ]; then
    echo "FAIL real bootloader version-bump check (not built, and SECTOR0_REQUIRE_REAL=1)"; fail=1
fi

exit "$fail"
