"""Render the transient operator notice in the top status bar, headlessly.

WHY THIS EXISTS. Same reason as previews/preview_takeup_banner.py and
previews/preview_phase_offset.py: the notice is a pinned overlay, and no unit
test in this repo can assert on a rendered layout (tests/components patch
apply_class_lang_rules out because the mock GL backend segfaults on real
textures). The one honest test of an overlay is a picture at the target
1024x600, plus the numbers behind it.

AND THE ONE RULE IT HAS TO PROVE. From els_advbar.kv, Evan 2026-08-22: "having
things resize around a temporary warning is distracting." A transient message
may COVER; it may not move or resize anything. That claim is checked here the
way preview_phase_offset.py checks it -- same widget objects, same rectangles,
notice off and on -- rather than eyeballed.

THE WARNING SHOT'S TEXT IS PRODUCED BY PRODUCTION CODE. It is not typed in: the
ELS Z axis is unmapped and the real Engage button intent
(ElsUiController.toggle_engage) is invoked, so the refusal, the severity and the
sentence are all the ones an operator gets. That path is exactly the migration
this surface was built to prove -- before it, pressing Engage with no Z axis
mapped did nothing at all and said so only in a log file.

The info shots go through the production notify() API with illustrative text,
because the app's two info-severity call sites are double-tap race guards that
cannot be reached without standing the FSM up in a state the preview has no
business forcing. The LONG one is deliberate: it shows where the strip runs out
of room and how the ellipsis lands, which is the thing to know before writing
the next notice message.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_status_notice.py
"""
import os
import tempfile

# Before any kivy/reflex import: isolate the config dir (the app persists widget
# state, and a preview must not edit the developer's saved settings) and force
# the target-hardware size, which Kivy fixes at Window creation.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-notice-preview-")
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
from reflex.utils.notices import NOTICE_INFO, NOTICE_WARNING  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
MODE_ELS = 2
AXIS_NAMES = ("Z", "X", "S")
IDLE_TICKS = 8

# Long enough to run off the end of the strip on purpose -- see the module note.
LONG_INFO = ("Carriage retracted past the start position and the cycle is "
             "ready for another pass at the current depth of cut")

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


def _bar():
    return _find(lambda w: type(w).__name__ == "StatusBar")


def settle(n=IDLE_TICKS):
    for _ in range(n):
        EventLoop.idle()


def shot(name):
    out = os.path.join(OUT_DIR, f"status_notice_{name}.png")
    settle()
    app.root.export_to_png(out)   # first export under-renders; export twice
    app.root.export_to_png(out)
    print("WROTE", out)
    return out


def _walk(w, out=None):
    if out is None:
        out = []
    out.append(w)
    for c in w.children:
        _walk(c, out)
    return out


def _sizes():
    """Every widget on screen, by identity, with its rectangle.

    Identity (id()) rather than position in the tree, so the comparison is
    between the SAME objects before and after -- a widget that was rebuilt
    rather than moved would otherwise slip through as "unchanged".
    """
    return {id(w): (type(w).__name__, round(w.width), round(w.height),
                    round(w.x), round(w.y))
            for w in _walk(app.root)}


def assert_nothing_moved(before, after, tag, exempt):
    """Nothing but the notice strip itself may change size OR position."""
    bad = []
    for k, v in after.items():
        if k in exempt:
            continue
        old = before.get(k)
        if old is not None and old != v:
            bad.append(f"{v[0]}: {old[1:]} -> {v[1:]}")
    if bad:
        print(f"  !! {tag}: FAIL -- {len(bad)} widget(s) changed")
        for line in bad[:12]:
            print(f"       {line}")
    else:
        print(f"  {tag}: PASS -- nothing else changed size or position")


def _rows(w, depth=0, out=None):
    if out is None:
        out = []
    out.append((depth, type(w).__name__, round(w.x), round(w.y), round(w.width),
                round(w.height), getattr(w, "text", "")))
    for c in reversed(w.children):
        _rows(c, depth + 1, out)
    return out


def dump(tag):
    """Every rectangle inside the status bar, in window coordinates.

    The question is not "does it fit" but "what is underneath it" -- the
    placement argument for the strip is that it borrows the three developer
    counters and leaves the COM lamp showing, and that is checkable here.
    """
    bar = _bar()
    print(f"\n===== {tag} =====")
    print(f"  StatusBar h={round(bar.height)} y={round(bar.y)} "
          f"top={round(bar.top)} x={round(bar.x)} w={round(bar.width)}")
    print(f"  {'widget':<30} {'x':>5} {'y':>5} {'w':>5} {'h':>5}  text")
    for d, name, x, y, w, h, text in _rows(bar):
        text = (text or "").replace("\n", " ")[:40]
        print(f"  {'  ' * d}{name:<{30 - 2 * d}} {x:>5} {y:>5} {w:>5} {h:>5}  {text}")

    overlay = bar.ids.notice_overlay
    gutter = bar.ids.telemetry
    com = bar.ids.led_com
    print(f"  notice_overlay x={round(overlay.x)} y={round(overlay.y)} "
          f"w={round(overlay.width)} h={round(overlay.height)} "
          f"opacity={overlay.opacity}")

    # The overlay must stay INSIDE the gutter it borrows, and must not reach
    # back over the COM lamp -- the one item in that gutter an operator acts on,
    # and the one whose answer ("is the controller talking?") is the most likely
    # cause of the first message migrated onto this surface.
    if round(overlay.x) < round(com.right) - 1:
        print(f"  !! COVERS THE COM LAMP: overlay x={round(overlay.x)} "
              f"< com.right={round(com.right)}")
    if (round(overlay.right) > round(gutter.right) + 1
            or round(overlay.x) < round(gutter.x) - 1
            or round(overlay.top) > round(gutter.top) + 1
            or round(overlay.y) < round(gutter.y) - 1):
        print(f"  !! OUTSIDE THE TELEMETRY GUTTER: overlay "
              f"{round(overlay.x)}..{round(overlay.right)} x "
              f"{round(overlay.y)}..{round(overlay.top)} vs gutter "
              f"{round(gutter.x)}..{round(gutter.right)} x "
              f"{round(gutter.y)}..{round(gutter.top)}")

    label = bar.ids.label_notice
    if label.text:
        # How much of the message actually made it onto the screen. NOT
        # texture_size, which is useless here: with `shorten` on and a
        # constrained text_size, the texture is rendered AT the label width
        # whether or not anything was cut, so it always compares equal. Ask the
        # core label how wide the full string WOULD be instead.
        try:
            natural = label._label.get_extents(label.text)[0]
        except Exception:
            natural = -1
        fits = int(round(len(label.text) * label.width / natural)) if natural > 0 else -1
        print(f"  label w={round(label.width)} natural_w={round(natural)} "
              f"chars={len(label.text)} fits~{fits}"
              f"{'   <-- TRUNCATED, ellipsised' if 0 < label.width < natural else ''}")


def _clear_notice():
    """Take the current notice down through the production API, not by poking
    the properties -- a preview that sets notice_text directly would render a
    strip the NoticeCenter does not know about."""
    app.els_uic._notices.clear()
    app.els_uic._publish_notice()
    settle()


def _capture(_dt):
    bar = _bar()
    # The strip and its two labels are what APPEARS, so they are exempt from the
    # nothing-moved check. `.__self__` because kv ids hold WeakProxy wrappers and
    # id(proxy) != id(widget) -- preview_phase_offset.py learned that the hard
    # way, with an exemption that matched nothing.
    exempt = {id(bar.ids.notice_overlay.__self__),
              id(bar.ids.label_notice.__self__),
              id(bar.ids.label_notice_icon.__self__)}

    try:
        # UNMAP THE ELS Z AXIS FIRST, and settle, so the config change is fully
        # absorbed BEFORE the baseline snapshot. Otherwise its own relayout
        # would show up in the nothing-moved comparison as if the notice had
        # caused it.
        app.els.z_axis_index = -1
        settle()

        _clear_notice()
        dump("no notice")
        shot("off")
        before = _sizes()

        # ── The real thing: the production Engage intent, refused. ──
        app.els_uic.toggle_engage()
        settle()
        print(f"\n  toggle_engage -> severity={app.els_uic.notice_severity!r} "
              f"text={app.els_uic.notice_text!r}")
        dump("warning (real refusal from toggle_engage)")
        assert_nothing_moved(before, _sizes(), "warning notice", exempt)
        shot("warning")

        # ── The other severity. ──
        _clear_notice()
        app.els_uic.notify("Carriage retracted — ready to cut", NOTICE_INFO)
        settle()
        dump("info")
        assert_nothing_moved(before, _sizes(), "info notice", exempt)
        shot("info")

        # ── Deliberately too long: where does it run out of room? ──
        _clear_notice()
        app.els_uic.notify(LONG_INFO, NOTICE_INFO)
        settle()
        dump("info (over-long, expect an ellipsis)")
        assert_nothing_moved(before, _sizes(), "over-long notice", exempt)
        shot("info_long")

        # ── And back to nothing: the strip must leave no trace behind it. ──
        _clear_notice()
        dump("cleared")
        assert_nothing_moved(before, _sizes(), "after clearing", exempt)
        shot("cleared")
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
    els_bar = app.manager.get_screen("home").els_bar
    els_bar.enable_advanced = True

    def _stoponly(_d):
        # AFTER the mode swap mounts the bar -- setting it in _arm silently does
        # nothing (preview_banner_placements.py learned this the hard way).
        adv = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
        if adv is not None:
            adv.enable_wizard = False
            adv.enable_retract = False
        Clock.schedule_once(_capture, 1.0)

    Clock.schedule_once(_stoponly, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
