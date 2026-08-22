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
    """Both operator modes, warning off and on.

    THE MODE MATTERS AND ASSUMING IT DID NOT COST A WRONG DIAGNOSIS. The
    advanced bar's control row is `size_hint_y: 1.0` in stop-only and `0.8`
    (with a 0.2 wizard strip) in wizard mode, so the two modes divide the bar's
    height by different arithmetic. A fresh config comes up in WIZARD mode; the
    machine is run in STOP-ONLY, which is where the banner was reported landing
    on top of the spindle DRO row. Render both or measure the wrong screen.
    """
    bar = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")

    def settle(n=IDLE_TICKS):
        for _ in range(n):
            EventLoop.idle()

    def show(tag):
        app.els_uic.takeup_warning = ""
        settle()
        _dump(f"{tag} / WARNING OFF")
        _shot(f"{tag}_off")
        app.els_uic.takeup_warning = WARNING
        settle()
        _dump(f"{tag} / WARNING ON")
        _shot(f"{tag}_on")

    try:
        # _arm already set stop-only BEFORE the first layout, so this is the
        # boot state of a machine whose saved config is stop-only.
        show("stoponly-fresh")

        # Into wizard and back out. This is the path an operator takes with the
        # wizard toggle, and it is the difference between "stop-only is broken"
        # and "leaving wizard mode is broken" -- which are different fixes.
        if bar is not None:
            bar.enable_wizard = True
        settle()
        show("wizard")

        if bar is not None:
            bar.enable_wizard = False
        settle()
        show("stoponly-toggled")

        # DISCRIMINATOR. Is the wizard strip's stale 26px simply a layout that
        # never re-ran, or does size_hint_y == 0 genuinely leave the height
        # alone? Force the layout and look again: if the strip drops to 0 the
        # bug is a missed relayout on mode change; if it stays 26 the mixed
        # fixed/proportional children are the cause and no relayout can save
        # them. Different fixes, so this is worth one more render.
        if bar is not None:
            bar.do_layout()
            settle()
            fl = [c for c in bar.children if type(c).__name__ == "FloatLayout"]
            print(f"\n>>> AFTER FORCED do_layout(): wizard strip shy="
                  f"{fl[0].size_hint_y if fl else '?'} height="
                  f"{round(fl[0].height) if fl else '?'}")
        show("stoponly-relaid")
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
    home = app.manager.get_screen("home")
    home.els_bar.enable_advanced = True
    # Stop-only BEFORE the first layout pass: a fresh config defaults to wizard,
    # and the machine is run in stop-only. Setting it later would measure a
    # toggled bar, which is a different state (see _capture).
    adv = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
    if adv is not None:
        adv.enable_wizard = False
        adv.enable_retract = False
    Clock.schedule_once(_capture, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
