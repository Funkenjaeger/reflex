#!/bin/sh
# Launch wrapper for reflex-ui, invoked by reflex-ui.service.
#
# LOCATION-AGNOSTIC since the 2026-08-17 monorepo: every path derives from
# this script's own resolved location, so the same file works from the old
# /reflex-ui layout and from <monorepo>/ui/deploy — the service's symlink
# (/reflex-ui/start.sh) just points wherever the live checkout is, and the
# unit's WorkingDirectory is overridden by the explicit cd below.
#
# Kivy renders directly via KMS/DRM on the Pi (no X server), so no DISPLAY is set.
# These KCFG_* vars configure Kivy via its environment-variable config overrides.
export KCFG_KIVY_KEYBOARD_MODE="systemanddock"
# NOT /var/log. The service still runs as root today, so Kivy's logs landed there
# root-owned and accumulated -- 80 stray kivy_*.txt files in a system directory as
# of 2026-09-01, unreadable to the operator account and to any rebuild. This
# directory is owned by the service user instead, which is also the direction the
# pi-gen image is going: see elspi.git RUNTIME-INVENTORY.md, "Decided 2026-09-01".
# Created defensively so a fresh deploy cannot fail on a directory that does not
# exist yet -- Kivy aborts if its log dir is missing, and on this machine that
# means a lathe with no UI and no terminal to fix it from.
export KCFG_KIVY_LOG_DIR="/var/log/reflex"
mkdir -p "$KCFG_KIVY_LOG_DIR" 2>/dev/null || true
export KCFG_GRAPHICS_WIDTH=1024
export KCFG_GRAPHICS_HEIGHT=600
export KCFG_GRAPHICS_FULLSCREEN=auto

# The service runs as root (DRM/KMS + /var/log writes), which would otherwise put
# the COMMISSIONED machine config -- axis geometry, servo polarity, calibration --
# in /root/.config/reflex where the operator account cannot read it. That left the
# one copy of this machine's real settings impossible to diff against defaults,
# back up on a schedule, or notice drifting. Keep it outside root's home instead.
# The directory is created with the service's umask (0755) so it stays readable
# without loosening the mode on /root or adding a standing sudo rule.
# Default when unset is ~/.config/reflex -- see reflex/utils/paths.py.
export REFLEX_CONFIG_DIR=/var/lib/reflex-config

# Resolve the ui checkout this script lives in (deploy/ -> its parent).
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
UI_DIR=$(dirname -- "$SCRIPT_DIR")

# Activate the checkout's own venv (created by `uv sync`) and run from source.
# The explicit cd puts the reflex package on sys.path regardless of the unit's
# WorkingDirectory.
. "$UI_DIR/.venv/bin/activate"
cd "$UI_DIR"
exec python -m reflex.main
