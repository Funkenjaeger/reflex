"""Render the ELS take-up refusal banner headlessly, and dump the real layout
geometry around it.

WHY THIS EXISTS. The banner has been "fixed" about six times and reported wrong
from the lathe every time, because the only way anyone ever saw the result was
Evan walking to the machine. tests/components/test_els_advbar.py patches
apply_class_lang_rules out (the mock GL backend segfaults on real textures), so
no unit test can assert on a rendered layout. This boots the REAL app under
xvfb at the target 1024x600, in ELS mode with the advanced bar expanded, and
prints every widget's rectangle in window coordinates with the warning off and
then on -- so the next change is judged against numbers and a picture instead
of a memory of a screen.

Setup mirrors scripts/capture_readme_screenshots.py, which already knows how to
reach this screen from a fresh config.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_takeup_banner.py
"""
import os
import tempfile

# Before any kivy/reflex import: isolate the config dir (the app persists widget
# state, and a preview must not edit the developer's saved settings) and force
# the target-hardware size, which Kivy fixes at Window creation.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-banner-preview-")
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
from reflex.utils.devices import ELS_TAKEUP_MESSAGES, ELS_TAKEUP_ERR_UNCONFIRMED  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
MODE_ELS = 2
AXIS_NAMES = ("Z", "X", "S")
IDLE_TICKS = 8

# The real string the operator sees, not a stand-in: its length is what drives
# the strip's height, which is the whole subject here.
WARNING = ELS_TAKEUP_MESSAGES[ELS_TAKEUP_ERR_UNCONFIRMED]

app = MainApp()

# Widgets worth printing. The point is the ELS bar and anything that could be
# pushed around by it; the full tree is ~300 rows of button internals.
KEEP = ("ElsAdvancedBar", "ElsBar", "ElsModeLayout", "HomePage", "CoordBar",
        "ElsSpindleInfo", "StatusBar", "ElsAxisBar")


def _rows(w, depth=0, out=None):
    if out is None:
        out = []
    try:
        wx, wy = w.to_window(w.x, w.y)
    except Exception:
        wx, wy = (-1, -1)
    out.append((depth, type(w).__name__, round(w.x), round(w.y), round(w.width),
                round(w.height), round(wx), round(wy), getattr(w, "size_hint_y", None)))
    for c in reversed(w.children):
        _rows(c, depth + 1, out)
    return out


def _dump(tag):
    print(f"\n===== {tag} =====")
    print(f"{'widget':<26} {'x':>5} {'y':>5} {'w':>5} {'h':>5} {'win_y':>6} {'shy':>6}")
    for d, name, x, y, w, h, wx, wy, shy in _rows(app.root):
        if not any(k in name for k in KEEP):
            continue
        print(f"{'  ' * d}{name:<{26 - 2 * d}} {x:>5} {y:>5} {w:>5} {h:>5} "
              f"{wy:>6} {str(shy):>6}")
    bar = _find(lambda x: type(x).__name__ == "ElsAdvancedBar")
    if bar is not None:
        print(f"  ElsAdvancedBar natural_height={getattr(bar, 'natural_height', None)} "
              f"height={round(bar.height)} top={round(bar.top)} y={round(bar.y)}")
        for c in reversed(bar.children):
            print(f"    child {type(c).__name__:<20} y={round(c.y):>5} h={round(c.height):>4} "
                  f"top={round(c.top):>5} shy={getattr(c, 'size_hint_y', None)} "
                  f"OUTSIDE={'YES' if (round(c.top) > round(bar.top) + 1 or round(c.y) < round(bar.y) - 1) else 'no'}")


def _find(pred, root=None):
    root = root or app.root
    if pred(root):
        return root
    for c in root.children:
        f = _find(pred, c)
        if f is not None:
            return f
    return None


def _shot(name):
    out = os.path.join(OUT_DIR, f"banner_{name}.png")
    for _ in range(IDLE_TICKS):
        EventLoop.idle()
    app.root.export_to_png(out)   # first export under-renders; export twice
    app.root.export_to_png(out)
    print("WROTE", out)


def _capture(_dt):
    try:
        _dump("WARNING OFF")
        _shot("off")

        app.els_uic.takeup_warning = WARNING
        for _ in range(IDLE_TICKS):
            EventLoop.idle()

        _dump("WARNING ON")
        _shot("on")
    except Exception:
        import traceback
        traceback.print_exc()
    app.stop()


def _arm(_dt):
    app.use_case = "lathe"
    for i, name in enumerate(AXIS_NAMES):
        if i < len(app.axes):
            app.axes[i].axis_name = name
    app.els.z_axis_index = 0
    app.els.x_axis_index = 1
    app.els.spindle_axis_index = 2
    app.manager.goto("home")
    app.set_mode(MODE_ELS)
    app.manager.get_screen("home").els_bar.enable_advanced = True
    Clock.schedule_once(_capture, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
