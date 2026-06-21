"""Boot the real app and export the live Home screen to verify the facelift
widgets integrate without runtime errors (no board connection required).

Run:
    DISPLAY=:0 SDL_AUDIODRIVER=dummy KIVY_INPUT=mouse \
        uv run python previews/preview_home_live.py
"""
import os
import sys

import reflex
from kivy.resources import resource_add_path

resource_add_path(os.path.dirname(reflex.__file__))

from kivy.clock import Clock
from kivy.base import EventLoop

# Force a window size matching the target hardware.
sys.argv = [sys.argv[0], "--size=1024x600"]

from reflex.app import MainApp

app = MainApp()


def _shot(_dt):
    out = os.path.join(os.path.dirname(__file__), "preview_home_live.png")
    try:
        for _ in range(4):
            EventLoop.idle()
        app.root.export_to_png(out)
        app.root.export_to_png(out)
        print("WROTE", out, "root size", app.root.size)
    except Exception as e:  # noqa: BLE001 - preview script, want the traceback
        import traceback
        traceback.print_exc()
        print("SHOT FAILED", e)
    app.stop()


def _arm(_dt):
    # Navigate to the home screen if not already there.
    try:
        app.manager.goto("home")
    except Exception:
        pass
    Clock.schedule_once(_shot, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
