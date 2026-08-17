"""Capture README screenshots of the home screen in ELS mode (advanced bar
expanded) for each UI theme, with no hardware connected.

Renders at the fixed target-hardware resolution (1024x600) and writes one PNG
per theme, composited over a black background (``export_to_png`` produces a
transparent background, which reads as white in many viewers; the app's real
``Window.clearcolor`` is black).

Run locally:
    DISPLAY=:0 SDL_AUDIODRIVER=dummy uv run python scripts/capture_readme_screenshots.py

Headless (as CI does):
    xvfb-run -a -s "-screen 0 1024x600x24" SDL_AUDIODRIVER=dummy \
        uv run python scripts/capture_readme_screenshots.py

Output files (in OUT_DIR, default current directory):
    home_els_light.png
    home_els_dark.png
"""
import os
import tempfile

# Run against an isolated, empty config home so the capture is deterministic
# (identical in CI and locally) and never reads or pollutes a developer's real
# ~/.config/reflex. The app supports a fresh install; we configure the bits we
# need (use case, axes, theme) at runtime below. Must be set before any Kivy or
# reflex import, since Path.home()/~ are resolved from HOME.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-shots-home-")

# Force the target-hardware size BEFORE Kivy parses argv or creates a Window.
# Setting it via Config after the app imports is too late (Kivy reads argv and
# builds the Window at import). KIVY_NO_ARGS keeps Kivy from consuming our argv.
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
from PIL import Image  # noqa: E402

from reflex.app import MainApp  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR", ".")
THEMES = ("light", "dark")
MODE_ELS = 2
AXIS_NAMES = ("Z", "X", "S")  # representative lathe axes (S = spindle) for the showcase
IDLE_TICKS = 6  # frames to flush before each export (texture/colors must settle)

app = MainApp()


def _composite_over_black(path):
    """Flatten a transparent PNG onto an opaque black background, in place."""
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
    Image.alpha_composite(bg, img).convert("RGB").save(path)


def _capture(_dt):
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        for theme in THEMES:
            app.formats.theme = theme
            out = os.path.join(OUT_DIR, f"home_els_{theme}.png")
            for _ in range(IDLE_TICKS):
                EventLoop.idle()
            # First export under-renders (only one tile); export twice.
            app.root.export_to_png(out)
            app.root.export_to_png(out)
            _composite_over_black(out)
            print("WROTE", out, "root size", app.root.size)
    except Exception as e:  # noqa: BLE001 - capture script, want the full traceback
        import traceback
        traceback.print_exc()
        print("CAPTURE FAILED", e)
    app.stop()


def _arm(_dt):
    # Lathe use case is what exposes ELS mode (set_mode silently falls back to
    # DRO otherwise — see app.allowed_modes / USE_CASE_MODES).
    app.use_case = "lathe"
    # Name representative axes (a fresh config seeds 4 unnamed "?" axes).
    for i, name in enumerate(AXIS_NAMES):
        if i < len(app.axes):
            app.axes[i].axis_name = name
    # ELS mode renders the Z/X DRO rows and spindle RPM by axis index (see
    # ElsModeLayout.build_axis_bars / ElsSpindleInfo), not by name. On a fresh
    # config these default to -1 (nothing shown), so point them at Z/X/spindle.
    app.els.z_axis_index = 0
    app.els.x_axis_index = 1
    app.els.spindle_axis_index = 2
    # Navigate to home, switch to ELS mode, expand the advanced bar.
    app.manager.goto("home")
    app.set_mode(MODE_ELS)
    app.manager.get_screen("home").els_bar.enable_advanced = True
    # Mode swap is deferred via Clock (see HomePage.change_mode); give it time
    # for the ELS layout to mount before capturing.
    Clock.schedule_once(_capture, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
