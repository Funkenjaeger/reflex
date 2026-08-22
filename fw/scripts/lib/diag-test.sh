#!/usr/bin/env bash
# Regression test for scripts/lib/diag.sh, run under the SAME shell options as
# its two callers (build.sh, flash.sh): every probe the registry lists must
# resolve, and a name the registry does not list must be refused.
#
# Why it exists: on 2026-08-21 `build.sh --diag=takeup-settle-v2` printed
# "unknown diagnostic probe" and then listed takeup-settle-v2 as available.
# diag_resolve piped diag_probe_list into `grep -q`, which exits at the first
# match; under `set -o pipefail` the producer's next write got EPIPE and the
# pipeline reported failure -- so every probe except the LAST one in Ramps.h
# was refused, deterministically. A test that only ran without pipefail could
# not have seen it, hence the options below mirror the callers exactly.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$REPO/scripts/lib/diag.sh"

fail=0
names="$(diag_probe_list "$REPO")"
[ -n "$names" ] || { echo "FAIL registry parsed as empty"; exit 1; }
while read -r name; do
    if macro="$(diag_resolve "$REPO" "$name")"; then
        echo "ok   $name -> $macro"
    else
        echo "FAIL $name is listed but does not resolve"
        fail=1
    fi
done <<<"$names"
if diag_resolve "$REPO" no-such-probe >/dev/null 2>&1; then
    echo "FAIL no-such-probe resolved"; fail=1
else
    echo "ok   no-such-probe refused"
fi
exit "$fail"
