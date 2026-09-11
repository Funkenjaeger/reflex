# Fabricated flash-region dumps for the sector-0 tests. Sourced by
# lib/sector0-test.sh and by the fake openocd in lib/flash-preflight-test.sh,
# so both build exactly the same bytes for the same name. Not executable.
#
# Every fixture stands in for a 128 KB dump of 0x08000000..0x0801FFFF, the
# region lib/sector0.py classifies. Offsets and vector values are the ones
# measured on the real images built 2026-09-11:
#
#   legacy build/reflex-fw.bin      MSP 0x20020000  reset 0x08004f6d
#                                   app identity window at +0x9a94
#   bootloader reflex-bl.bin        reset 0x0800140d, window at +0x1478,
#                                   5264 bytes then 0xFF to the end of sector 0
#   slotted build-slot/reflex-fw.bin  reset 0x08024ff5, window at +0x9b14
#
# The identity window's first three words are magic 0x454C, stage, window
# version -- little-endian, so the magic is the bytes 4c 45.

S0FX_SECTOR=16384

# n bytes of one value, as raw bytes. $2 is a printf octal escape.
s0fx_fill() { head -c "$1" /dev/zero | tr '\000' "$2"; }

# Overwrite bytes at an offset in place. $3 is a printf escape string.
s0fx_poke() {  # s0fx_poke <file> <offset> <printf-bytes>
    # shellcheck disable=SC2059
    printf "$3" | dd of="$1" bs=1 seek="$2" conv=notrunc status=none
}

s0fx_vectors() {  # s0fx_vectors <file> <reset-vector-printf-bytes>
    s0fx_poke "$1" 0 '\x00\x00\x02\x20'     # MSP 0x20020000, top of SRAM
    s0fx_poke "$1" 4 "$2"
}

s0fx_window() {  # s0fx_window <file> <offset> <stage 1|2> [<version lo byte hex>]
    s0fx_poke "$1" "$2" "\\x4c\\x45\\x0$3\\x00\\x${4:-01}\\x00"
}

# sector0_fixture <name> <out> [<size>]
sector0_fixture() {
    local name="$1" out="$2" size="${3:-131072}"
    case "$name" in
        erased)
            s0fx_fill "$size" '\377' > "$out" ;;
        legacy|legacy-v7|legacy-nowindow|legacy-oddbl)
            # Code all the way through: 0x2a filler has no 0xFF runs.
            s0fx_fill "$size" '\052' > "$out"
            s0fx_vectors "$out" '\x6d\x4f\x00\x08'
            case "$name" in
                legacy)          s0fx_window "$out" $((0x9a94)) 2 ;;
                legacy-v7)       s0fx_window "$out" $((0x9a94)) 2 07 ;;
                legacy-nowindow) : ;;
                legacy-oddbl)    s0fx_window "$out" $((0x9a94)) 2
                                 # A bootloader-window-LOOKING pattern at an
                                 # ODD offset: not a uint16_t array, not a window.
                                 s0fx_window "$out" $((0x0801)) 1 ;;
            esac ;;
        bootloader|bootloader-v2|bootloader-vff)
            { s0fx_fill 5264 '\052'; s0fx_fill $(( size - 5264 )) '\377'; } > "$out"
            s0fx_vectors "$out" '\x0d\x14\x00\x08'
            case "$name" in
                bootloader)     s0fx_window "$out" $((0x1478)) 1 ;;
                bootloader-v2)  s0fx_window "$out" $((0x1478)) 1 02 ;;
                bootloader-vff) s0fx_poke "$out" $((0x1478)) '\x4c\x45\x01\x00\xff\xff' ;;
            esac ;;
        bootloader-at-end)
            # The window in the last six bytes of sector 0: a scan that
            # stopped short of the sector's edge would miss it.
            { s0fx_fill 5264 '\052'; s0fx_fill $(( size - 5264 )) '\377'; } > "$out"
            s0fx_vectors "$out" '\x0d\x14\x00\x08'
            s0fx_window "$out" $(( S0FX_SECTOR - 6 )) 1 ;;
        converted|foreign)
            # A legacy board that was provisioned: the bootloader over sector
            # 0, the journal sector erased, and the legacy app's leftovers --
            # its identity window included -- still in sectors 2 and 3.
            sector0_fixture legacy "$out" "$size"
            s0fx_fill "$S0FX_SECTOR" '\377' \
                | dd of="$out" bs=1 seek=0 conv=notrunc status=none
            s0fx_fill "$S0FX_SECTOR" '\377' \
                | dd of="$out" bs=1 seek="$S0FX_SECTOR" conv=notrunc status=none
            s0fx_fill 5264 '\052' | dd of="$out" bs=1 seek=0 conv=notrunc status=none
            s0fx_vectors "$out" '\x0d\x14\x00\x08'
            # `foreign` is the same board with a bootloader that carries no
            # Reflex identity window -- the case the old guard waved through.
            [ "$name" = converted ] && s0fx_window "$out" $((0x1478)) 1
            : ;;
        slot-at-zero)
            # The slotted application programmed at 0x08000000 by mistake:
            # its reset vector points into the RUN slot, not at itself.
            s0fx_fill "$size" '\052' > "$out"
            s0fx_vectors "$out" '\xf5\x4f\x02\x08'
            s0fx_window "$out" $((0x9b14)) 2 ;;
        *)
            echo "sector0_fixture: unknown fixture '$name'" >&2
            return 99 ;;
    esac
}
