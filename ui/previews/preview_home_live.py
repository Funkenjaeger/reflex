"""Boot the real app and export the live Home screen to verify the facelift
widgets integrate without runtime errors (no board connection required).

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" \\
        ./.venv/bin/python previews/preview_home_live.py

TWO BREAKS FIXED 2026-08-30, both silent, both of the kind that make a preview
write a confident picture of the wrong thing:

  * IT WAS NOT RENDERING AT 1024x600. The size was requested by rewriting
    `sys.argv` to `--size=1024x600` -- but every other harness here, and the
    documented run recipe, export `KIVY_NO_ARGS=1`, whose entire purpose is to
    make Kivy IGNORE argv. So the flag was read by nothing and the app came up
    at the SDL default 800x600, i.e. 224 px narrower than the machine. Every
    shot this script has produced was a picture of a screen that does not
    exist, and it exited 0 each time. Now set through `Config` before the Kivy
    import, the way the other six previews do it, and ASSERTED after the fact
    -- a size that silently fails is exactly what happened last time.
  * IT WROTE TO THE DEVELOPER'S REAL CONFIG. Alone among the previews it did
    not isolate `HOME`, so booting MainApp persisted its dispatcher YAML
    (use case, axes, theme) into `~/.config/reflex`. A preview must not edit
    the settings of the person running it.
"""
import os
import tempfile

# Before any kivy/reflex import: isolate the config dir and force the target
# hardware size, which Kivy fixes at Window creation.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-home-live-")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from kivy.config import Config  # noqa: E402

Config.set("graphics", "width", "1024")
Config.set("graphics", "height", "600")

import reflex  # noqa: E402
from kivy.resources import resource_add_path  # noqa: E402

resource_add_path(os.path.dirname(reflex.__file__))

from kivy.base import EventLoop  # noqa: E402
from kivy.clock import Clock  # noqa: E402

from reflex.app import MainApp  # noqa: E402

TARGET_SIZE = [1024, 600]
FAILED = []

app = MainApp()


def _shot(_dt):
    out = os.path.join(os.path.dirname(__file__), "preview_home_live.png")
    try:
        for _ in range(4):
            EventLoop.idle()
        app.root.export_to_png(out)
        app.root.export_to_png(out)
        print("WROTE", out, "root size", app.root.size)
        # THE SIZE IS DEMANDED, NOT HOPED FOR. This shot was 800x600 for as
        # long as the script existed because the size was requested through a
        # channel (argv) that KIVY_NO_ARGS switches off, and nothing ever
        # compared the result against the target.
        assert list(app.root.size) == TARGET_SIZE, (
            f"rendered at {list(app.root.size)}, not the machine's "
            f"{TARGET_SIZE} -- this is a picture of a screen nobody owns. "
            f"Check that Config.set ran before the first kivy.core.window "
            f"import.")
    except Exception as e:  # noqa: BLE001 - preview script, want the traceback
        import traceback
        traceback.print_exc()
        print("SHOT FAILED", e)
        FAILED.append(e)
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

# A HARNESS THAT WRITES A FILE AND EXITS 0 IS NOT EVIDENCE IT WORKED. The
# except above exists so the traceback is readable rather than swallowed by
# Kivy's clock, but it must not also swallow the exit code -- that is what
# let an 800x600 render pass for a 1024x600 one run after run.
if FAILED:
    raise SystemExit(f"preview_home_live failed: {FAILED[0]}")
