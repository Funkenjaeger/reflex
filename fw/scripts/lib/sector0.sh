# Sector-0 occupancy check. Sourced by scripts/flash.sh and
# scripts/provision.sh; not executable.
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
# that was just used to destroy it. That happened to elspi on 2026-09-07.
#
# THE SIGNATURE. fw/bootloader/src/main.c declares
#
#   static const uint16_t blIdentityWindow[ELS_ID_SIZE] =
#       ELS_ID_WINDOW_INIT(ELS_ID_STAGE_BOOTLOADER, 0u);
#
# `const`, so it is in .rodata, so it is IN SECTOR 0 -- not a runtime value we
# would need a live Modbus link to read. Its first three words are fixed by
# Core/Inc/els_identity.h and are the same for every bootloader build:
#
#   [0] ELS_ID_MAGIC          0x454C   -> bytes 4c 45
#   [1] ELS_ID_STAGE_BOOTLOADER   1    -> bytes 01 00
#   [2] ELS_ID_WINDOW_VERSION     1    -> bytes 01 00
#
# The words after those three are the build rev and dirty flag, which vary, so
# the signature stops at six bytes: 4c 45 01 00 01 00.
#
# Why the STAGE word is load-bearing. The application declares the same window
# (Core/Src/Ramps.c) with ELS_ID_STAGE_APP = 2, so its bytes are 4c 45 02 00
# 01 00. Matching on the magic alone would fire on an application image and
# refuse to flash the very board this script exists for. Measured against the
# binaries built on 2026-09-07:
#
#   bootloader/build/reflex-bl.bin   5264 B   stage-1 sig at 0x1478, no stage-2
#   build/reflex-fw.bin  (legacy)   41892 B   no stage-1 sig; stage-2 at 0x9a84
#   build-slot/reflex-fw.bin        42036 B   no stage-1 sig; stage-2 at 0x9b14
#
# Note where the legacy application's own window sits: 0x9a84 is past the end
# of sector 0 (0x4000), so scanning only the 16 KB of sector 0 never sees it
# at all. The stage discrimination is belt and braces on top of that.
#
# WHY A SEPARATE FILE. The scan takes a FILE, not a target, so it can be
# exercised against hand-made dumps with no board, no ST-Link and no openocd
# in the room -- see lib/sector0-test.sh. A guard that has only ever been run
# against hardware nobody dares reproduce the failure on is a guard nobody
# knows the state of.

# The stage-1 identity window prefix, as the lower-case hex byte string that
# sector0_scan_hex produces. Six bytes; see above for why not more and not
# fewer.
SECTOR0_BL_SIGNATURE_HEX=4c4501000100

# Size of sector 0 on the STM32F411CE, matching ELS_BL_SECTOR_SIZE.
SECTOR0_SIZE=16384
SECTOR0_BASE=0x08000000

# Render a binary file as one unbroken lower-case hex string.
#
# od rather than xxd: od is coreutils and is on the Pi already, xxd ships with
# vim and is not guaranteed to be.
#
# -v because without it od replaces runs of identical OUTPUT LINES with '*',
# and sector 0 of a board carrying the 5 KB bootloader is ~11 KB of 0xff.
# Measured 2026-09-08: dropping -v does NOT break the substring search, and
# the test says so -- a collapsed run is by definition all-identical lines, so
# the line holding the signature is never one of the ones elided, and no false
# negative is reachable that way. What it does break is the promise this
# function's name makes: the output stops being a faithful byte-for-byte
# rendering, it gains a literal '*', and any future caller that measures a
# length or an offset from it is silently wrong. lib/sector0-test.sh asserts
# the rendering is complete rather than merely searchable, so -v has a test
# that goes red for it.
sector0_scan_hex() {
    od -An -v -tx1 -- "$1" | tr -d ' \n'
}

# True if the dump in $1 carries the bootloader's identity window.
#
# The match is on a hex STRING, so in principle it could land on an odd byte
# boundary. Six specific bytes straddling a byte boundary by chance is not a
# risk worth code to exclude, and the error it would cause is a refusal to
# flash -- the safe direction. A false NEGATIVE would be the dangerous one, and
# a substring search cannot produce one.
sector0_has_bootloader() {
    local dump="$1"
    [ -r "$dump" ] || return 2
    sector0_scan_hex "$dump" | grep -q "$SECTOR0_BL_SIGNATURE_HEX"
}

# The refusal text, printed by both callers so the wording only exists once.
# $1 is the name of the override flag the calling script offers.
sector0_refuse_message() {
    cat <<EOF
REFUSING TO FLASH: this board is carrying the field bootloader.

Sector 0 (${SECTOR0_BASE}, 16 KB) holds the bootloader's identity window, so
the application on this board lives in the RUN slot at 0x08020000 behind it.
Programming the legacy 0x08000000 image over the top would overwrite the
bootloader's vector table and destroy it. openocd would report success.

What you almost certainly want instead:

  ./scripts/provision.sh              rebuild and reprogram bootloader + app
  python3 scripts/modbus-flash.py build-slot/reflex-fw.bin --port /dev/ttyUSB0
                                      update the app over RS-485, no programmer

If you really are taking this board BACK to the legacy no-bootloader layout --
a deliberate act, and the bootloader's write protection has to be cleared for
it to even succeed (fw/bootloader/README.md step 9b with 'off') -- then say so:

  ./scripts/flash.sh $1
EOF
}
