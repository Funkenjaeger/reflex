#!/usr/bin/env bash
# End-to-end test for the sector-0 preflight PLUMBING in scripts/flash.sh.
#
# WHAT THIS COVERS THAT lib/sector0-test.sh DOES NOT. That test proves the
# DECISION: given a 128 KB dump file, lib/sector0.py classifies it correctly.
# It says nothing about how flash.sh PRODUCES that file or what it does with
# the answer -- the openocd invocation, the --host dump-and-scp-back path, the
# dump-length check, and the fail-closed behaviour when any of it goes wrong.
# All of that is the part that stands between a bootloaded board and an
# overwritten sector 0, and until this file existed none of it had ever run
# outside a session with a probe and a board attached.
#
# Since 2026-09-24 it also runs scripts/provision.sh's preflight the same way,
# for its sector-0 write-protection read (lib/sector0-optcr.cfg -> OPTCR line
# -> sector0_wrp_reported), locally and over --host.
#
# HOW, WITH NO PROBE AND NO BOARD. A fake `openocd`, `ssh` and `scp` are put
# first on PATH. The fake openocd honours the `dump_image <path> <base> <size>`
# arguments well enough to write a chosen file, and FAKE_OPENOCD_MODE switches
# it between writing a bootloader-carrying sector 0, a legacy one, a truncated
# one, and failing outright. flash.sh is then run end to end and asserted on
# its exit code, its distinguishing message, AND the fake openocd's call log --
# the log is what makes "the guard ran BEFORE anything was written" an
# assertion rather than an assumption.
#
# The real openocd is never run, and neither is a real ssh: every run asserts
# that `command -v openocd` (and ssh/scp for the --host arms) resolves inside
# this test's own fixture directory before it invokes flash.sh. There is a real
# machine on this network that this script must never touch.
#
# SEEN RED. Verified 2026-09-08 by mutating scripts/flash.sh three ways -- the
# fail-closed branch made unreachable so a failed dump proceeds, the
# dump-length check deleted, and the whole preflight block moved to after the
# program step -- and confirming each turns arms of this test red. Again on
# 2026-09-11 for the allowlist rewrite: the remote-dump delete removed, the
# post-read `reset run` removed, and the verdict `case` given a catch-all
# proceed -- each turns its own arms red (journal 2026-09-11). A test that has
# only ever been green proves nothing about a guard.
#
# Run under the same shell options as the callers, per lib/diag-test.sh.
#
#   bash scripts/lib/flash-preflight-test.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FLASH="$REPO/scripts/flash.sh"
FIXTURES="$REPO/scripts/lib/sector0-fixtures.sh"
# shellcheck source=sector0-fixtures.sh
. "$FIXTURES"
# shellcheck source=diag.sh
. "$REPO/scripts/lib/diag.sh"

TMP="$(mktemp -d)"
BIN="$TMP/bin"          # the fakes, first on PATH
REMOTE="$TMP/remote"    # stands in for the probe host's home directory
FAKEHOME="$TMP/home"    # so ~/firmware is not created in a real home
mkdir -p "$BIN" "$REMOTE" "$FAKEHOME"

OPENOCD_LOG="$TMP/openocd.log"
SSH_LOG="$TMP/ssh.log"
SCP_LOG="$TMP/scp.log"

STUB_ELFS=()
cleanup() {
    rm -rf "$TMP"
    local f
    for f in "${STUB_ELFS[@]+"${STUB_ELFS[@]}"}"; do rm -f "$f"; done
}
trap cleanup EXIT

fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }

# flash.sh refuses to go anywhere without a built ELF, and --no-build does not
# change that. Stand one in when the tree is not built, so this test runs in a
# fresh checkout the way lib/sector0-test.sh does. Its contents never matter:
# nothing here reads it except md5sum and scp. The diagnostic arm needs one in
# that probe's build directory too.
# Capture, then take the first line: `diag_probe_list | head -1` under
# pipefail is the SIGPIPE race lib/diag.sh documents at diag_resolve.
DIAG_PROBES="$(diag_probe_list "$REPO")"
DIAG_PROBE="${DIAG_PROBES%%$'\n'*}"
stub_elf() {
    local path="$REPO/$1/reflex-fw.elf"
    if [ ! -f "$path" ]; then
        mkdir -p "$REPO/$1"
        printf 'stub ELF written by scripts/lib/flash-preflight-test.sh\n' > "$path"
        STUB_ELFS+=("$path")
    fi
}
stub_elf build
stub_elf "$(diag_build_dir "$DIAG_PROBE")"

# --- the fakes --------------------------------------------------------------

cat > "$BIN/openocd" <<'FAKE_OPENOCD'
#!/usr/bin/env bash
# Fake openocd. Parses the `dump_image <path> <base> <size>` out of the -c
# script flash.sh builds and writes that file itself; FAKE_OPENOCD_MODE chooses
# what it writes -- any fixture name from lib/sector0-fixtures.sh -- or one of
# three failures. Logs one line per invocation so the caller can assert what
# was run and in what order, and whether the core was let run afterwards.
set -uo pipefail
mode="${FAKE_OPENOCD_MODE:-legacy}"
log_file="${FAKE_OPENOCD_LOG:-/dev/null}"
# shellcheck source=/dev/null
. "${FAKE_FIXTURES:?}"

dump_path=""; dump_base=""; dump_size=""; saw_program=0; resumed=no
optcr_cfg=""; prev=""
for a in "$@"; do
    # provision.sh's preflight: `-f .../sector0-optcr.cfg`.
    if [ "$prev" = -f ]; then
        case "$a" in *sector0-optcr.cfg) optcr_cfg="$a" ;; esac
    fi
    prev="$a"
    case "$a" in
        *dump_image*)
            rest="${a#*dump_image }"
            # Whatever follows the dump in the same script. A `reset run` there
            # is what restarts a core that `reset halt` stopped.
            case "${rest#*;}" in *"reset run"*) resumed=yes ;; esac
            rest="${rest%%;*}"
            read -r dump_path dump_base dump_size _ <<<"$rest" ;;
    esac
    case "$a" in
        *"program "*) saw_program=1 ;;
    esac
done

# The OPTCR read. Real openocd resolves -f against its cwd, so a path that does
# not exist where openocd runs -- the --host case with the script not copied
# over -- is an error, as it would be for real. Otherwise print what the cfg's
# echo would (FAKE_OPTCR: a value, `none` or `unreadable`), to stderr like
# openocd's log, and record whether the script lets the core run after it.
if [ -n "$optcr_cfg" ]; then
    if [ ! -f "$optcr_cfg" ]; then
        printf 'optcr cfg=missing path=%s\n' "$optcr_cfg" >> "$log_file"
        echo "fake openocd: Error: cannot open $optcr_cfg" >&2
        exit 1
    fi
    if awk '/read_memory/ { r = 1 } r && /^reset run/ { f = 1 } END { exit !f }' "$optcr_cfg"; then
        resumed=yes
    fi
    printf 'optcr cfg=present resumed=%s value=%s\n' "$resumed" "${FAKE_OPTCR:-none}" >> "$log_file"
    s0fx_optcr_output "${FAKE_OPTCR:-none}" >&2
    exit 0
fi

if [ -n "$dump_path" ]; then
    printf 'dump_image %s %s %s mode=%s resumed=%s\n' \
        "$dump_path" "$dump_base" "$dump_size" "$mode" "$resumed" >> "$log_file"
    case "$mode" in
        fail)
            echo "fake openocd: Error: init mode failed (unable to connect to the target)" >&2
            exit 1 ;;
        short)
            # A dump that started and stopped early. Readable, wrong length.
            s0fx_fill 100 '\377' > "$dump_path" ;;
        nowrite)
            # Exit 0 and write NOTHING. Whether real openocd can do this is
            # unverified; the guard must not depend on it being impossible.
            : ;;
        *)
            sector0_fixture "$mode" "$dump_path" "$dump_size" || exit 99 ;;
    esac
    exit 0
fi

if [ "$saw_program" = 1 ]; then
    printf 'program mode=%s\n' "$mode" >> "$log_file"
    exit 0
fi

printf 'unrecognised %s\n' "$*" >> "$log_file"
exit 0
FAKE_OPENOCD

cat > "$BIN/ssh" <<'FAKE_SSH'
#!/usr/bin/env bash
# Fake ssh. Runs the command locally with HOME and cwd pointed at a directory
# standing in for the probe host, so relative remote paths and ~/firmware land
# somewhere this test can inspect. No network, ever.
set -uo pipefail
log_file="${FAKE_SSH_LOG:-/dev/null}"
while [ $# -gt 0 ]; do
    case "$1" in
        -o) shift 2 ;;
        -*) shift ;;
        *)  break ;;
    esac
done
host="${1:-}"; [ $# -gt 0 ] && shift
printf 'ssh %s -- %s\n' "$host" "$*" >> "$log_file"
if [ -z "${FAKE_REMOTE_HOME:-}" ]; then
    echo "fake ssh: FAKE_REMOTE_HOME is not set -- refusing to run anything" >&2
    exit 255
fi
[ $# -eq 0 ] && exit 0
cd "$FAKE_REMOTE_HOME" || exit 255
HOME="$FAKE_REMOTE_HOME" exec bash -c "$*"
FAKE_SSH

cat > "$BIN/scp" <<'FAKE_SCP'
#!/usr/bin/env bash
# Fake scp. Rewrites host:path to a path under FAKE_REMOTE_HOME and copies.
# FAKE_SCP_MODE=fail makes the transfer fail the way a truncated or refused
# copy would.
set -uo pipefail
log_file="${FAKE_SCP_LOG:-/dev/null}"
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        -o) shift 2 ;;
        -*) shift ;;
        *)  args+=("$1"); shift ;;
    esac
done
printf 'scp %s\n' "${args[*]}" >> "$log_file"
if [ "${FAKE_SCP_MODE:-ok}" = fail ]; then
    echo "fake scp: simulated transfer failure" >&2
    exit 1
fi
resolve() {
    case "$1" in
        [A-Za-z]:[\\/]*) printf '%s' "$1" ;;   # a Windows drive path, local
        *:*)             printf '%s/%s' "${FAKE_REMOTE_HOME:?}" "${1#*:}" ;;
        *)               printf '%s' "$1" ;;
    esac
}
src="$(resolve "${args[0]}")"
dst="$(resolve "${args[1]}")"
mkdir -p "$(dirname "$dst")"
cp -- "$src" "$dst"
FAKE_SCP

chmod +x "$BIN/openocd" "$BIN/ssh" "$BIN/scp"

# --- harness ----------------------------------------------------------------

OUT=""
RC=0

# Nothing runs until the fakes are provably what the name resolves to. A test
# that silently fell through to a real openocd -- or a real ssh -- would be
# worse than no test.
assert_fakes_resolve() {
    local t r
    for t in openocd ssh scp; do
        r="$(export PATH="$BIN:$PATH"; command -v "$t" || true)"
        if [ "$r" != "$BIN/$t" ]; then
            bad "fake $t is not what resolves on PATH (got '${r:-nothing}')"
            return 1
        fi
    done
    return 0
}

STALE_REMOTE_DUMP=""   # set to a fixture name to leave one where --host dumps

run_flash() {  # run_flash <openocd-mode> <scp-mode> [flash.sh args...]
    local omode="$1" smode="$2"; shift 2
    : > "$OPENOCD_LOG"; : > "$SSH_LOG"; : > "$SCP_LOG"
    rm -rf "$REMOTE" "$FAKEHOME"; mkdir -p "$REMOTE" "$FAKEHOME"
    if [ -n "$STALE_REMOTE_DUMP" ]; then
        mkdir -p "$REMOTE/firmware"
        sector0_fixture "$STALE_REMOTE_DUMP" "$REMOTE/firmware/sector0-preflight.bin"
    fi
    assert_fakes_resolve || return 0
    OUT="$(cd "$REPO" && env \
        PATH="$BIN:$PATH" \
        HOME="$FAKEHOME" \
        FAKE_FIXTURES="$FIXTURES" \
        FAKE_OPENOCD_MODE="$omode" \
        FAKE_OPENOCD_LOG="$OPENOCD_LOG" \
        FAKE_SCP_MODE="$smode" \
        FAKE_SCP_LOG="$SCP_LOG" \
        FAKE_SSH_LOG="$SSH_LOG" \
        FAKE_REMOTE_HOME="$REMOTE" \
        bash "$FLASH" "$@" 2>&1)" && RC=0 || RC=$?
}

dump_out() { printf '%s\n' "$OUT" | sed 's/^/       | /'; }

check_rc() {  # check_rc <want> <what>
    if [ "$RC" = "$1" ]; then ok "$2 -> exit $RC"
    else bad "$2 -> exit $RC (wanted $1)"; dump_out; fi
}
# Here-strings, not `printf | grep -q`: under pipefail a SIGPIPE on the printf
# makes the pipeline false, and in check_lacks false reads as a PASS.
check_has() {  # check_has <substring> <what>
    if grep -qF -- "$1" <<<"$OUT"; then ok "$2"
    else bad "$2 -- missing: $1"; dump_out; fi
}
check_lacks() {  # check_lacks <substring> <what>
    if grep -qF -- "$1" <<<"$OUT"; then bad "$2 -- unexpectedly present: $1"; dump_out
    else ok "$2"; fi
}
# The sequence of openocd operations, as a single space-separated string.
check_openocd_seq() {  # check_openocd_seq <want> <what>
    local got
    got="$(cut -d' ' -f1 "$OPENOCD_LOG" | tr '\n' ' ' | sed 's/ *$//')"
    if [ "$got" = "$1" ]; then ok "$2 -> [$got]"
    else bad "$2 -> openocd ran [$got], wanted [$1]"; dump_out; fi
}
check_openocd_log_has() {  # check_openocd_log_has <substring> <what>
    if grep -qF -- "$1" "$OPENOCD_LOG"; then ok "$2"
    else bad "$2 -- openocd log lacks: $1"; sed 's/^/       | /' "$OPENOCD_LOG"; fi
}

echo "== sector-0 preflight plumbing in scripts/flash.sh =="

REFUSE_BL="REFUSING TO FLASH: this board is carrying the field bootloader"
REFUSE_READ="REFUSING TO FLASH: could not read the flash"
REFUSE_UNKNOWN="REFUSING TO FLASH: the flash at 0x08000000 is not a layout"
SAID_LEGACY="legacy application recognized at 0x08000000 -- safe to program"
SAID_ERASED="sector 0 is erased -- nothing there to destroy"

# Every refusal must also NOT have said either of the two proceed lines -- a
# script that printed "safe to program" and then refused would be a script
# whose output nobody could trust.
refused_cleanly() {  # refused_cleanly <what>
    check_lacks "$SAID_LEGACY" "$1 -- does NOT report the board as legacy"
    check_lacks "$SAID_ERASED" "$1 -- does NOT report the board as erased"
    check_lacks "DRY RUN" "$1 -- never reaches the write preview"
}

# 1. BOOTLOADER IN SECTOR 0. The dump succeeds and carries the identity window:
#    refuse, and say which refusal it is.
run_flash bootloader ok --no-build --dry-run
check_rc 1 "bootloader in sector 0"
check_has "$REFUSE_BL" "bootloader in sector 0 -- prints the bootloader refusal"
refused_cleanly "bootloader in sector 0"
check_openocd_seq "dump_image" "bootloader in sector 0 -- read only, no program"

# 1b. THE REGRESSION THE REWRITE EXISTS FOR, end to end: a bootloader whose
#     identity window is at version 2. The old six-byte signature missed it and
#     reported the sector clear.
run_flash bootloader-v2 ok --no-build --dry-run
check_rc 1 "bootloader, window version 2"
check_has "$REFUSE_BL" "bootloader, window version 2 -- still the bootloader refusal"
refused_cleanly "bootloader, window version 2"

# 2. LEGACY. The one board flash.sh still exists for, positively recognized.
run_flash legacy ok --no-build --dry-run
check_rc 0 "legacy board"
check_has "$SAID_LEGACY" "legacy board -- says so"
check_has "DRY RUN" "legacy board -- gets past the preflight to the preview"

# 2b. ERASED. Nothing to destroy; proceeds, and points at provision.sh.
run_flash erased ok --no-build --dry-run
check_rc 0 "erased sector 0"
check_has "$SAID_ERASED" "erased sector 0 -- says so"
check_has "should be PROVISIONED" "erased sector 0 -- points at provision.sh"
check_has "DRY RUN" "erased sector 0 -- gets to the preview"

# 3. UNKNOWN. What the old denylist waved through: a bootloader with no Reflex
#    identity window, over a legacy app's leftovers. The allowlist refuses it,
#    and the refusal carries the classifier's reasons.
run_flash foreign ok --no-build --dry-run
check_rc 1 "unidentified bootloader"
check_has "$REFUSE_UNKNOWN" "unidentified bootloader -- the unknown-content refusal"
check_has "run of 0xFF" "unidentified bootloader -- says which test it failed"
refused_cleanly "unidentified bootloader"

run_flash slot-at-zero ok --no-build --dry-run
check_rc 1 "slotted app at the wrong address"
check_has "$REFUSE_UNKNOWN" "slotted app at the wrong address -- unknown-content refusal"
refused_cleanly "slotted app at the wrong address"

# 4. THE DUMP FAILS. FAIL-CLOSED: openocd not installed, no ST-Link, target
#    held in reset and read protection all land on this branch. A read that
#    did not happen is not evidence of anything, and must never be treated as
#    a clean board.
run_flash fail ok --no-build --dry-run
check_rc 1 "openocd exits non-zero"
check_has "$REFUSE_READ" "openocd exits non-zero -- refuses as unknown"
refused_cleanly "openocd exits non-zero"

# 5. THE DUMP IS SHORT. openocd exits 0 but the file is the wrong length -- a
#    read that started and stopped. Same unknown, same refusal.
run_flash short ok --no-build --dry-run
check_rc 1 "openocd writes a short dump"
check_has "$REFUSE_READ" "openocd writes a short dump -- refuses as unknown"
refused_cleanly "openocd writes a short dump"

# 5b. openocd exits 0 and writes NOTHING. Locally the dump is a fresh, empty
#     mktemp, so the length check catches it.
run_flash nowrite ok --no-build --dry-run
check_rc 1 "openocd exits 0 without writing (local)"
check_has "$REFUSE_READ" "openocd exits 0 without writing (local) -- refuses as unknown"
refused_cleanly "openocd exits 0 without writing (local)"

# 6. --force-legacy. The operator overriding the guard on a board that really
#    is carrying the bootloader: skip the preflight entirely, and say plainly
#    what that costs. openocd must not be asked to read anything at all.
run_flash bootloader ok --no-build --dry-run --force-legacy
check_rc 0 "--force-legacy with a bootloader present"
check_has "--force-legacy: skipping the sector-0 preflight" \
          "--force-legacy -- says it skipped the preflight"
check_has "WILL destroy it" "--force-legacy -- prints the destruction warning"
check_has "DRY RUN" "--force-legacy -- proceeds to the write preview"
check_openocd_seq "" "--force-legacy -- no dump taken"

# 7. A diagnostic build on a bootloaded board: refused like any other, and
#    pointed at the route that works (slotted diag build, over the wire).
run_flash bootloader ok --no-build --dry-run "--diag=$DIAG_PROBE"
check_rc 1 "--diag on a bootloaded board"
check_has "$REFUSE_BL" "--diag on a bootloaded board -- the bootloader refusal"
check_has "-DREFLEX_APP_BASE=0x08020000" "--diag on a bootloaded board -- names the slotted diag build"
check_has "-DELS_DIAG_PROBE=ELS_DIAG_SCHEMA_" "--diag on a bootloaded board -- with the probe's macro resolved"

# --- the --host path --------------------------------------------------------
#
# The dump is taken on the remote machine, to a path in the remote home, and
# scp'd back before anything decides anything. The local temp file starts
# EMPTY, so a decision reached on it at all is proof the bytes travelled: if
# the copy had not landed, the length check would have made it "unknown" and
# arm 8a would print the could-not-read refusal instead of the bootloader one.

# 8a. Remote bootloader -> the bootloader refusal, decided locally on the file
#     that came back.
run_flash bootloader ok --no-build --dry-run --host fakehost
check_rc 1 "--host, bootloader on the remote board"
check_has "$REFUSE_BL" "--host -- decides on the dump that came back, not on the empty local temp"
check_has "from the board on fakehost" "--host -- reads on the remote machine"
check_openocd_log_has "dump_image firmware/sector0-preflight.bin" \
          "--host -- the dump was written on the remote, at the remote path"
if grep -qF "fakehost:firmware/sector0-preflight.bin" "$SCP_LOG"; then
    ok "--host -- the dump was scp'd back before the decision"
else
    bad "--host -- no scp of the remote dump"; sed 's/^/       | /' "$SCP_LOG"
fi

# 8b. Remote legacy board -> proceeds, same as local.
run_flash legacy ok --no-build --dry-run --host fakehost
check_rc 0 "--host, legacy remote board"
check_has "$SAID_LEGACY" "--host, legacy remote board -- says so"
check_has "DRY RUN" "--host, legacy remote board -- gets to the write preview"
if [ ! -e "$REMOTE/firmware/sector0-preflight.bin" ]; then
    ok "--host -- the remote dump is removed after it is copied back"
else
    bad "--host -- the remote dump was left behind for the next run to find"
fi

# 8c. The scp fails. The dump exists on the remote and says "bootloader", but
#     it never arrives, so locally this is unknown -- and unknown refuses.
run_flash bootloader fail --no-build --dry-run --host fakehost
check_rc 1 "--host, scp of the dump fails"
check_has "$REFUSE_READ" "--host, scp fails -- refuses as unknown"
refused_cleanly "--host, scp fails"

# 8d. THE STALE REMOTE DUMP. A previous run left a LEGACY dump -- right length,
#     perfectly classifiable, from a board that may not be this one -- and this
#     time openocd exits 0 without writing. The old script copied the stale
#     file back and proceeded. It must refuse as a read that did not happen.
STALE_REMOTE_DUMP=legacy
run_flash nowrite ok --no-build --dry-run --host fakehost
STALE_REMOTE_DUMP=""
check_rc 1 "--host, stale legacy dump + openocd writes nothing"
check_has "$REFUSE_READ" "--host, stale dump -- refuses as unknown, not on the old bytes"
refused_cleanly "--host, stale dump"

# --- the controller is let run after the read -------------------------------
#
# `reset halt` stops the core for the dump. On a refusal and on --dry-run there
# is no write step to reset it afterwards, so unless the read itself ends with
# `reset run`, the lathe controller is left halted. Every dump in the arms
# above went through the fake, which logs whether one followed.
run_flash bootloader ok --no-build --dry-run
check_openocd_log_has "resumed=yes" "refusal -- the core is let run after the read"
run_flash legacy ok --no-build --dry-run
check_openocd_log_has "resumed=yes" "dry run -- the core is let run after the read"

# --- ordering: the guard runs BEFORE the write, not after -------------------
#
# Every arm above is a --dry-run, which stops before the write on its own. These
# run for real against the fakes, so the openocd call log is the evidence: a
# preflight that happened after the program step, or not at all, shows up here
# as a `program` line that should not be there.

# 9. Legacy board, full run: read first, then write. In that order.
run_flash legacy ok --no-build
check_rc 0 "full run on a legacy board"
check_openocd_seq "dump_image program" \
          "full run on a legacy board -- reads the flash, THEN programs"

# 10. Bootloaded board, and an unidentified one, full run: never written.
run_flash bootloader ok --no-build
check_rc 1 "full run on a bootloaded board"
check_has "$REFUSE_BL" "full run on a bootloaded board -- the bootloader refusal"
check_openocd_seq "dump_image" \
          "full run on a bootloaded board -- NOTHING was programmed"

run_flash foreign ok --no-build
check_rc 1 "full run on an unidentified board"
check_openocd_seq "dump_image" \
          "full run on an unidentified board -- NOTHING was programmed"

# --- provision.sh: the sector-0 write-protection read -----------------------
#
# provision.sh's reachability preflight runs lib/sector0-optcr.cfg and decides
# from the OPTCR line it prints. Until 2026-09-24 it ran `flash info 0`, which
# the lathe's openocd never prints anything for, so the warning could not fire
# on any board. These run provision.sh end to end, --dry-run, against the same
# fakes, feeding each OPTCR answer (FAKE_OPTCR) and asserting what it SAYS.

echo
echo "== sector-0 write-protection read in scripts/provision.sh =="

PROVISION="$REPO/scripts/provision.sh"

# provision.sh refuses without a built bootloader ELF and a HEADERED slotted
# image (it runs reflex_image.py info on it). Stand in for both when the tree
# is not built, and remove them afterwards, like stub_elf above.
stub_provision_inputs() {
    local bl="$REPO/bootloader/build/reflex-bl.elf" app="$REPO/build-slot/reflex-fw.bin"
    if [ ! -f "$bl" ]; then
        mkdir -p "$(dirname "$bl")"
        STUB_ELFS+=("$bl")
        printf 'stub ELF written by scripts/lib/flash-preflight-test.sh\n' > "$bl"
    fi
    if [ ! -f "$app" ]; then
        mkdir -p "$(dirname "$app")"
        STUB_ELFS+=("$app")
        python3 - "$app" <<'PY'
import struct, sys
img = bytearray(b"\xff" * 0x400)
# magic "RFLX", header v1, flags 0; length and CRC are patched below
struct.pack_into("<IHHIII3I", img, 0x200, 0x584C4652, 1, 0, 0, 0, 0, 0, 0, 0)
open(sys.argv[1], "wb").write(img)
PY
        python3 "$REPO/scripts/reflex_image.py" patch "$app" >/dev/null
    fi
}
stub_provision_inputs

run_provision() {  # run_provision <optcr|none|unreadable> [provision.sh args...]
    local optcr="$1"; shift
    : > "$OPENOCD_LOG"; : > "$SSH_LOG"; : > "$SCP_LOG"
    rm -rf "$REMOTE" "$FAKEHOME"; mkdir -p "$REMOTE" "$FAKEHOME"
    assert_fakes_resolve || return 0
    OUT="$(cd "$REPO" && env \
        PATH="$BIN:$PATH" \
        HOME="$FAKEHOME" \
        FAKE_FIXTURES="$FIXTURES" \
        FAKE_OPTCR="$optcr" \
        FAKE_OPENOCD_LOG="$OPENOCD_LOG" \
        FAKE_SCP_LOG="$SCP_LOG" \
        FAKE_SSH_LOG="$SSH_LOG" \
        FAKE_REMOTE_HOME="$REMOTE" \
        bash "$PROVISION" "$@" 2>&1)" && RC=0 || RC=$?
}

SAID_WRP="sector 0 is WRITE-PROTECTED"
SAID_CLEAR="sector 0 is not write-protected"
SAID_UNKNOWN="could not read sector 0's write protection"

# 11. Sector 0 protected (the real reading with nWRP0 clear): warn, with the
#     value as evidence and the clearing command.
run_provision 0x0ffeaacd --no-build --dry-run
check_rc 0 "provision, sector 0 protected"
check_has "$SAID_WRP" "provision, sector 0 protected -- warns before the erase"
check_has "FLASH_OPTCR=0x0ffeaacd" "provision, sector 0 protected -- prints the register read"
check_has "flash protect 0 0 0 off" "provision, sector 0 protected -- gives the clearing command"
check_lacks "$SAID_CLEAR" "provision, sector 0 protected -- does NOT also call it clear"
check_lacks "$SAID_UNKNOWN" "provision, sector 0 protected -- does NOT call it unknown"
check_openocd_log_has "optcr cfg=present resumed=yes" \
          "provision -- reads OPTCR through the cfg, and the core is let run after"

# 12. Nothing protected (the real reading): says so, no warning.
run_provision 0x0fffaacd --no-build --dry-run
check_rc 0 "provision, nothing protected"
check_has "$SAID_CLEAR (FLASH_OPTCR=0x0fffaacd)" "provision, nothing protected -- says sector 0 is clear"
check_lacks "$SAID_WRP" "provision, nothing protected -- no protection warning"
check_lacks "$SAID_UNKNOWN" "provision, nothing protected -- not unknown"

# 13. Another sector protected, not sector 0: sector 0 is clear.
run_provision 0x0ffdaacd --no-build --dry-run
check_has "$SAID_CLEAR" "provision, sector 1 protected -- sector 0 still clear"
check_lacks "$SAID_WRP" "provision, sector 1 protected -- no sector-0 warning"

# 14. No OPTCR line -- what every run of the old flash-info probe produced on
#     the lathe. Must say it could not tell, not stay silent, not claim either.
run_provision none --no-build --dry-run
check_rc 0 "provision, no OPTCR line"
check_has "$SAID_UNKNOWN" "provision, no OPTCR line -- says the state could not be read"
check_lacks "$SAID_WRP" "provision, no OPTCR line -- does NOT claim protected"
check_lacks "$SAID_CLEAR" "provision, no OPTCR line -- does NOT claim clear"

# 15. --host: openocd runs on the probe host, so the cfg must be there. The
#     fake openocd fails on a -f path that does not exist in its cwd.
run_provision 0x0ffeaacd --no-build --dry-run --host fakehost
check_rc 0 "provision --host, sector 0 protected"
check_has "$SAID_WRP" "provision --host -- the protection read works remotely"
check_openocd_log_has "optcr cfg=present" "provision --host -- the cfg exists where openocd runs"
if grep -qF "fakehost:firmware/sector0-optcr.cfg" "$SCP_LOG"; then
    ok "provision --host -- the cfg was copied to the probe host"
else
    bad "provision --host -- no scp of the cfg"; sed 's/^/       | /' "$SCP_LOG"
fi

exit "$fail"
