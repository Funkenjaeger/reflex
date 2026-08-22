"""Render placement candidates for the ELS take-up refusal banner.

Six attempts at this were made blind, then fixed for real on 2026-08-22 once a
headless render existed (previews/preview_takeup_banner.py). The strip no
longer escapes the advanced bar -- but WHERE it should live, and whether the
DRO rows should pay for it, are Evan's calls, not defects. So: render the
options and let him point at one.

All three are applied as live tree surgery on the running app. NOTHING here
touches production kv -- these are pictures of proposals, and only the chosen
one gets written into the widget.

    A  bottom of the advanced bar, bar grows
       The strip moves from the top of the bar (hard against the spindle DRO
       row) down to sit directly on top of the control row, where the
       operator's eyes already are when they press Cut. The bar still grows by
       30 px, so the three DRO rows still give up 10 px each.

    B  bottom of the advanced bar, bar does NOT grow
       Same placement, but the bar keeps its 128 px and the CONTROL ROW absorbs
       the strip instead of the DRO rows. Nothing above the ELS bar moves at
       all when a warning appears -- which is the "shrink for no reason"
       complaint answered. Cost: the control row is 30 px shorter while a
       warning is up.

    C  top of the screen, under the status bar
       Out of the ELS area entirely and treated as a system message. Furthest
       from the controls, and the DRO rows still pay the 30 px.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_banner_placements.py
"""
import os
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-placements-")
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
WARNING = ELS_TAKEUP_MESSAGES[ELS_TAKEUP_ERR_UNCONFIRMED]

app = MainApp()


def _find(pred, root=None):
    root = root or app.root
    if pred(root):
        return root
    for c in root.children:
        f = _find(pred, c)
        if f is not None:
            return f
    return None


def settle(n=IDLE_TICKS):
    for _ in range(n):
        EventLoop.idle()


def shot(name):
    out = os.path.join(OUT_DIR, f"placement_{name}.png")
    settle()
    app.root.export_to_png(out)
    app.root.export_to_png(out)
    print("WROTE", out)


def rows(tag):
    """The numbers that decide B against A and C: does anything above the ELS
    bar move when the warning appears?"""
    bar = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
    dro = [w for w in _walk(app.root)
           if type(w).__name__ in ("DroCoordBar", "ElsSpindleInfo")]
    print(f"  {tag:<22} bar h={round(bar.height) if bar else '?':<4} "
          f"DRO row heights={[round(w.height) for w in dro]}")


def _walk(w, out=None):
    if out is None:
        out = []
    out.append(w)
    for c in w.children:
        _walk(c, out)
    return out


def _capture(_dt):
    bar = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
    mode_layout = _find(lambda w: type(w).__name__ == "ElsModeLayout")
    notice = bar.ids.takeup_notice
    natural_kv = bar.property("natural_height")   # to restore between variants

    try:
        app.els_uic.takeup_warning = ""
        settle()
        rows("baseline, no warning")
        shot("0_none")

        # ── current (landed fix): top of the bar ──────────────────────────
        app.els_uic.takeup_warning = WARNING
        settle()
        rows("CURRENT top-of-bar")
        shot("1_current")

        # ── A: bottom of the bar, bar still grows ─────────────────────────
        bar.remove_widget(notice)
        bar.add_widget(notice, index=0)          # index 0 == bottom of a vbox
        settle()
        rows("A bottom-of-bar")
        shot("2_A_bottom_of_bar")

        # ── B: same, but the control row absorbs it ───────────────────────
        # natural_height is a kv binding; unbind by assigning, then pin it.
        bar.unbind_property("natural_height") if hasattr(bar, "unbind_property") else None
        bar.natural_height = 128
        settle()
        rows("B controls absorb")
        shot("3_B_controls_absorb")

        # ── C: top of the screen, under the status bar ────────────────────
        # The bar must go back to its BASE height here: the strip is leaving it
        # entirely, so a grown bar plus a new 30 px sibling over-subscribes the
        # mode layout and the strip collides with the status bar -- which is
        # what the first render of this variant showed, and it was a bug in the
        # variant, not in the idea.
        bar.natural_height = 128
        bar.remove_widget(notice)
        mode_layout.add_widget(notice, index=len(mode_layout.children))
        settle()
        rows("C under status bar")
        shot("4_C_under_statusbar")
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

    def _stoponly(_d):
        # AFTER the mode swap mounts the bar -- setting it in _arm silently does
        # nothing, and stop-only is the mode the machine is actually run in.
        adv = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
        if adv is not None:
            adv.enable_wizard = False
            adv.enable_retract = False
        Clock.schedule_once(_capture, 1.0)

    Clock.schedule_once(_stoponly, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
