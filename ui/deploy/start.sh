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

# Activate the project venv (created by `uv sync`) and run from source.
# WorkingDirectory=/reflex-ui (set in the unit) puts the reflex package on sys.path.
. /reflex-ui/.venv/bin/activate
exec python -m reflex.main
