#!/usr/bin/env bash
# End-to-end test for the sector-0 preflight PLUMBING in scripts/flash.sh.
#
# WHAT THIS COVERS THAT lib/sector0-test.sh DOES NOT. That test proves the
# DECISION: given a 16 KB dump file, sector0_has_bootloader answers correctly.
# It says nothing about how flash.sh PRODUCES that file or what it does with
# the answer -- the openocd invocation, the --host dump-and-scp-back path, the
# dump-length check, and the fail-closed behaviour when any of it goes wrong.
# All of that is the part that stands between a bootloaded board and an
# overwritten sector 0, and until this file existed none of it had ever run
# outside a session with a probe and a board attached.
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
# program step -- and confirming each turns arms of this test red. A test that
# has only ever been green proves nothing about a guard.
#
# Run under the same shell options as the callers, per lib/diag-test.sh.
#
#   bash scripts/lib/flash-preflight-test.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FLASH="$REPO/scripts/flash.sh"
# shellcheck source=sector0.sh
. "$REPO/scripts/lib/sector0.sh"

TMP="$(mktemp -d)"
BIN="$TMP/bin"          # the fakes, first on PATH
REMOTE="$TMP/remote"    # stands in for the probe host's home directory
FAKEHOME="$TMP/home"    # so ~/firmware is not created in a real home
mkdir -p "$BIN" "$REMOTE" "$FAKEHOME"

OPENOCD_LOG="$TMP/openocd.log"
SSH_LOG="$TMP/ssh.log"
SCP_LOG="$TMP/scp.log"

STUB_ELF=""
cleanup() {
    rm -rf "$TMP"
    if [ -n "$STUB_ELF" ]; then rm -f "$STUB_ELF"; fi
}
trap cleanup EXIT

fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }

# flash.sh refuses to go anywhere without a built ELF, and --no-build does not
# change that. Stand one in when the tree is not built, so this test runs in a
# fresh checkout the way lib/sector0-test.sh does. Its contents never matter:
# nothing here reads it except md5sum and scp.
ELF_PATH="$REPO/build/reflex-fw.elf"
if [ ! -f "$ELF_PATH" ]; then
    mkdir -p "$REPO/build"
    printf 'stub ELF written by scripts/lib/flash-preflight-test.sh\n' > "$ELF_PATH"
    STUB_ELF="$ELF_PATH"
fi

# --- the fakes --------------------------------------------------------------

cat > "$BIN/openocd" <<'FAKE_OPENOCD'
#!/usr/bin/env bash
# Fake openocd. Parses the `dump_image <path> <base> <size>` out of the -c
# script flash.sh builds and writes that file itself; FAKE_OPENOCD_MODE chooses
# what it writes, or whether it fails. Logs one line per invocation so the
# caller can assert what was run and in what order.
set -uo pipefail
mode="${FAKE_OPENOCD_MODE:-legacy}"
log_file="${FAKE_OPENOCD_LOG:-/dev/null}"

BL_SIG='\x4c\x45\x01\x00\x01\x00'   # stage 1, bootloader -- see lib/sector0.sh

filler() { head -c "$1" /dev/zero | tr '\000' "$2"; }

dump_path=""; dump_base=""; dump_size=""; saw_program=0
for a in "$@"; do
    case "$a" in
        *dump_image*)
            rest="${a#*dump_image }"
            rest="${rest%%;*}"
            read -r dump_path dump_base dump_size _ <<<"$rest" ;;
    esac
    case "$a" in
        *"program "*) saw_program=1 ;;
    esac
done

if [ -n "$dump_path" ]; then
    printf 'dump_image %s %s %s mode=%s\n' \
        "$dump_path" "$dump_base" "$dump_size" "$mode" >> "$log_file"
    case "$mode" in
        fail)
            echo "fake openocd: Error: init mode failed (unable to connect to the target)" >&2
            exit 1 ;;
        short)
            # A dump that started and stopped early. Readable, wrong length.
            filler 100 '\377' > "$dump_path" ;;
        bootloader)
            off=$((0x1478))   # where the window sits in the real bootloader
            { filler "$off" '\377'
              printf "$BL_SIG"
              filler $(( dump_size - off - 6 )) '\377'
            } > "$dump_path" ;;
        legacy)
            # Application code through sector 0, no identity window in it: the
            # legacy app's own stage-2 window lives at 0x9a84, past this sector.
            filler "$dump_size" '\052' > "$dump_path" ;;
        *)
            echo "fake openocd: unknown FAKE_OPENOCD_MODE=$mode" >&2
            exit 99 ;;
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

run_flash() {  # run_flash <openocd-mode> <scp-mode> [flash.sh args...]
    local omode="$1" smode="$2"; shift 2
    : > "$OPENOCD_LOG"; : > "$SSH_LOG"; : > "$SCP_LOG"
    rm -rf "$REMOTE" "$FAKEHOME"; mkdir -p "$REMOTE" "$FAKEHOME"
    assert_fakes_resolve || return 0
    OUT="$(cd "$REPO" && env \
        PATH="$BIN:$PATH" \
        HOME="$FAKEHOME" \
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
check_has() {  # check_has <substring> <what>
    if printf '%s' "$OUT" | grep -qF -- "$1"; then ok "$2"
    else bad "$2 -- missing: $1"; dump_out; fi
}
check_lacks() {  # check_lacks <substring> <what>
    if printf '%s' "$OUT" | grep -qF -- "$1"; then bad "$2 -- unexpectedly present: $1"; dump_out
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

# 1. BOOTLOADER IN SECTOR 0. The dump succeeds and carries the identity window:
#    refuse, and say which refusal it is.
run_flash bootloader ok --no-build --dry-run
check_rc 1 "bootloader in sector 0"
check_has "REFUSING TO FLASH: this board is carrying the field bootloader" \
          "bootloader in sector 0 -- prints the bootloader refusal"
check_lacks "DRY RUN" "bootloader in sector 0 -- never reaches the write preview"
check_openocd_seq "dump_image" "bootloader in sector 0 -- read only, no program"

# 2. LEGACY SECTOR 0. The one board flash.sh still exists for: proceed.
run_flash legacy ok --no-build --dry-run
check_rc 0 "legacy sector 0"
check_has "no bootloader in sector 0" "legacy sector 0 -- says so"
check_has "DRY RUN" "legacy sector 0 -- gets past the preflight to the preview"

# 3. THE DUMP FAILS. FAIL-CLOSED, and the most important arm here: openocd not
#    installed, no ST-Link, target held in reset and sector 0 write-protected
#    all land on this branch. A read that did not happen is not evidence of an
#    empty sector 0, and must never be treated as one.
run_flash fail ok --no-build --dry-run
check_rc 1 "openocd exits non-zero"
check_has "REFUSING TO FLASH: could not read sector 0" \
          "openocd exits non-zero -- refuses as unknown"
check_lacks "no bootloader in sector 0" \
            "openocd exits non-zero -- does NOT report the sector as clear"
check_lacks "DRY RUN" "openocd exits non-zero -- never reaches the write preview"

# 4. THE DUMP IS SHORT. openocd exits 0 but the file is the wrong length -- a
#    read that started and stopped. Same unknown, same refusal.
run_flash short ok --no-build --dry-run
check_rc 1 "openocd writes a short dump"
check_has "REFUSING TO FLASH: could not read sector 0" \
          "openocd writes a short dump -- refuses as unknown"
check_lacks "no bootloader in sector 0" \
            "openocd writes a short dump -- does NOT report the sector as clear"

# 5. --force-legacy. The operator overriding the guard on a board that really
#    is carrying the bootloader: skip the preflight entirely, and say plainly
#    what that costs. openocd must not be asked to read anything at all.
run_flash bootloader ok --no-build --dry-run --force-legacy
check_rc 0 "--force-legacy with a bootloader present"
check_has "--force-legacy: skipping the sector-0 preflight" \
          "--force-legacy -- says it skipped the preflight"
check_has "WILL destroy it" "--force-legacy -- prints the destruction warning"
check_has "DRY RUN" "--force-legacy -- proceeds to the write preview"
check_openocd_seq "" "--force-legacy -- no dump taken"

# --- the --host path --------------------------------------------------------
#
# The dump is taken on the remote machine, to a path in the remote home, and
# scp'd back before anything decides anything. The local temp file starts
# EMPTY, so a decision reached on it at all is proof the bytes travelled: if
# the copy had not landed, the length check would have made it "unknown" and
# arm 6 would print the could-not-read refusal instead of the bootloader one.

# 6a. Remote bootloader -> the bootloader refusal, decided locally on the file
#     that came back.
run_flash bootloader ok --no-build --dry-run --host fakehost
check_rc 1 "--host, bootloader on the remote board"
check_has "REFUSING TO FLASH: this board is carrying the field bootloader" \
          "--host -- decides on the dump that came back, not on the empty local temp"
check_has "from the board on fakehost" "--host -- reads on the remote machine"
check_openocd_log_has "dump_image firmware/sector0-preflight.bin" \
          "--host -- the dump was written on the remote, at the remote path"
if grep -qF "fakehost:firmware/sector0-preflight.bin" "$SCP_LOG"; then
    ok "--host -- the dump was scp'd back before the decision"
else
    bad "--host -- no scp of the remote dump"; sed 's/^/       | /' "$SCP_LOG"
fi

# 6b. Remote legacy board -> proceeds, same as local.
run_flash legacy ok --no-build --dry-run --host fakehost
check_rc 0 "--host, legacy remote board"
check_has "no bootloader in sector 0" "--host, legacy remote board -- says so"
check_has "DRY RUN" "--host, legacy remote board -- gets to the write preview"

# 6c. The scp fails. The dump exists on the remote and says "bootloader", but
#     it never arrives, so locally this is unknown -- and unknown refuses.
run_flash bootloader fail --no-build --dry-run --host fakehost
check_rc 1 "--host, scp of the dump fails"
check_has "REFUSING TO FLASH: could not read sector 0" \
          "--host, scp fails -- refuses as unknown"
check_lacks "no bootloader in sector 0" \
            "--host, scp fails -- does NOT report the sector as clear"

# --- ordering: the guard runs BEFORE the write, not after -------------------
#
# Every arm above is a --dry-run, which stops before the write on its own. These
# two run for real against the fakes, so the openocd call log is the evidence:
# a preflight that happened after the program step, or not at all, shows up here
# as a `program` line that should not be there.

# 7. Legacy board, full run: read first, then write. In that order.
run_flash legacy ok --no-build
check_rc 0 "full run on a legacy board"
check_openocd_seq "dump_image program" \
          "full run on a legacy board -- reads sector 0, THEN programs"

# 8. Bootloaded board, full run: the board is never written.
run_flash bootloader ok --no-build
check_rc 1 "full run on a bootloaded board"
check_has "REFUSING TO FLASH: this board is carrying the field bootloader" \
          "full run on a bootloaded board -- the bootloader refusal"
check_openocd_seq "dump_image" \
          "full run on a bootloaded board -- NOTHING was programmed"

exit "$fail"
