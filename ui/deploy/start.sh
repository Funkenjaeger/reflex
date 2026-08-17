#!/bin/sh
# Launch wrapper for reflex-ui, invoked by reflex-ui.service.
# Mirrors the original rotary-controller-python /start.sh, but points at the
# reflex package and the uv-managed virtualenv.
#
# Kivy renders directly via KMS/DRM on the Pi (no X server), so no DISPLAY is set.
# These KCFG_* vars configure Kivy via its environment-variable config overrides.
export KCFG_KIVY_KEYBOARD_MODE="systemanddock"
export KCFG_KIVY_LOG_DIR="/var/log"
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

# Activate the project venv (created by `uv sync`) and run from source.
# WorkingDirectory=/reflex-ui (set in the unit) puts the reflex package on sys.path.
. /reflex-ui/.venv/bin/activate
exec python -m reflex.main
