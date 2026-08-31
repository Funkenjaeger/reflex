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

FAILED = []

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

    THE MODE MATTERS AND ASSUMING IT DID NOT COST A WRONG DIAGNOSIS. A fresh
    config comes up in WIZARD mode; the machine is run in STOP-ONLY, which is
    where the banner was reported landing on top of the spindle DRO row.
    Render both or measure the wrong screen.

    The two modes used to divide the bar's height by different arithmetic
    (control row `size_hint_y: 1.0` in stop-only, `0.8` plus a 0.2 wizard
    strip in wizard), which is what made them tellable apart in the geometry
    dump. THAT IS NO LONGER TRUE: since the 2026-08-29 gutter redesign the bar
    is 128 px in both, the dump prints identical numbers for the two states,
    and the mode has to be asserted rather than read off. See `show()`.
    """
    bar = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")

    def settle(n=IDLE_TICKS):
        for _ in range(n):
            EventLoop.idle()

    def show(tag, wizard):
        """Both warning states of one bar mode, with the mode DEMANDED.

        `wizard` is not decoration: the mode is asserted here, at the moment
        of capture, because the 2026-08-29 gutter redesign left the bar the
        same height in both modes and the geometry dump can therefore no
        longer tell them apart. Until 2026-08-30 the 'stoponly-fresh' pair
        was silently captured in wizard mode; nothing in the dump said so.
        """
        assert bar is not None, f"no ElsAdvancedBar to capture {tag!r} against"
        assert bar.enable_wizard is wizard, (
            f"{tag}: bar is in {'wizard' if bar.enable_wizard else 'stop-only'} "
            f"mode but this shot is captured as "
            f"{'wizard' if wizard else 'stop-only'}")
        app.els_uic.takeup_warning = ""
        settle()
        _dump(f"{tag} / WARNING OFF")
        _shot(f"{tag}_off")
        app.els_uic.takeup_warning = WARNING
        settle()
        _dump(f"{tag} / WARNING ON")
        _shot(f"{tag}_on")

    try:
        # _stoponly() set and ASSERTED stop-only before this ran, so this is
        # the boot state of a machine whose saved config is stop-only.
        show("stoponly-fresh", wizard=False)

        # Into wizard and back out. This is the path an operator takes with the
        # wizard toggle, and it is the difference between "stop-only is broken"
        # and "leaving wizard mode is broken" -- which are different fixes.
        bar.enable_wizard = True
        settle()
        show("wizard", wizard=True)

        bar.enable_wizard = False
        settle()
        show("stoponly-toggled", wizard=False)

        # THE ORIGINAL DISCRIMINATOR IS GONE, AND SAYING SO IS THE POINT.
        # It asked whether the wizard strip's stale 26 px was a missed relayout
        # or mixed fixed/proportional children, by reading the size_hint_y of
        # "the FloatLayout child" after a forced do_layout(). The 2026-08-29
        # gutter redesign removed that strip; the only FloatLayout child left is
        # `bar_float`, the bar's own full-height container, so the line printed
        # "wizard strip shy=1 height=128" -- a confident answer about a widget
        # that does not exist. Retired 2026-08-30 rather than left to mislead.
        #
        # The relaid pair still earns its place: it is the one that would catch
        # a mode change that needs a forced layout to land.
        bar.do_layout()
        settle()
        print(f"\n>>> AFTER FORCED do_layout(): bar h={round(bar.height)} "
              f"natural_height={bar.natural_height} "
              f"(no separate wizard strip since 2026-08-29 -- the bar is the "
              f"same height in both modes, which is why `show()` asserts the "
              f"mode instead of inferring it from geometry)")
        show("stoponly-relaid", wizard=False)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        FAILED.append(exc)
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

    def _stoponly(_d):
        """Stop-only, AFTER the mode swap has mounted the bar.

        FIXED 2026-08-30. This ran inline in _arm(), immediately after
        set_mode() -- but HomePage.change_mode defers the mode swap through
        Clock, so ElsAdvancedBar was not in the tree yet, `_find` returned
        None, and the `if adv is not None:` guard swallowed it. The
        `stoponly-fresh` pair was therefore captured in WIZARD mode, the
        state a fresh config boots into, while the dump above it and the
        filename both said stop-only. It was invisible because the
        2026-08-29 gutter redesign made the bar the same HEIGHT in both
        modes, so the geometry dump -- the only thing anyone read -- prints
        identical numbers for the two states it is meant to tell apart.
        Caught by the transparent-pixel count of the exported PNGs being
        equal to the wizard shots' and unequal to stop-only's.

        preview_phase_offset.py and preview_status_notice.py already defer
        this and say so in a comment ("setting it in _arm silently does
        nothing"); so did preview_banner_placements.py before it was deleted
        on 2026-08-30. This one was the last holdout.

        THE ASSERT IS THE POINT. A guard that skips silently is how this
        survived, so the mode is now demanded rather than requested: if the
        bar is not there, or the flag does not take, the run dies instead of
        writing four confidently mislabelled pictures.
        """
        adv = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
        assert adv is not None, (
            "ElsAdvancedBar is not mounted yet -- every shot below would be "
            "captured in whatever mode a fresh config booted into, not the "
            "one in its filename. Raise the delay.")
        adv.enable_wizard = False
        adv.enable_retract = False
        assert adv.enable_wizard is False and adv.enable_retract is False, (
            f"stop-only did not take (enable_wizard={adv.enable_wizard!r}, "
            f"enable_retract={adv.enable_retract!r}) -- the 'stoponly-fresh' "
            f"shots would be a picture of some other mode.")
        Clock.schedule_once(_capture, 1.0)

    Clock.schedule_once(_stoponly, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()


# A HARNESS THAT WRITES FILES AND EXITS 0 IS NOT EVIDENCE IT WORKED.
# The try/except above exists so Kivy's clock cannot swallow the traceback --
# it must not also swallow the exit code. Added 2026-08-30, after a sibling
# preview wrote 2 of its 5 shots and still reported rc=0: the same shape that
# let preview_walkthrough_shots.py abandon a whole section unnoticed for a
# week. (That sibling, preview_banner_placements.py, was deleted the same day
# -- it rendered proposals for a banner the status gutter made impossible.)
if FAILED:
    raise SystemExit(f"{__file__}: capture failed: {FAILED[0]!r}")
