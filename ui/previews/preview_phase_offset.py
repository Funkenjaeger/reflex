"""Render the ELS thread-phase offset status strip headlessly.

WHY THIS EXISTS. Same reason as previews/preview_takeup_banner.py: the advanced
bar's notice strips are pinned overlays whose only honest test is a picture at
the target 1024x600, and tests/components/test_els_advbar.py patches
apply_class_lang_rules out (the mock GL backend segfaults on real textures) so
no unit test can assert on a rendered layout. The phase-offset strip raises the
stakes: it is PERSISTENT — up for a whole groove-widening job, not a few
seconds — so whatever it covers, it covers for the length of the job. That is a
judgment that has to be made against a rendering, not a memory of one.

THE TEXT IS PRODUCED BY PRODUCTION CODE, not typed in here. The HAL's step
read is stubbed (the preview has no board), and everything downstream of it is
the real thing: ElsFsm.phase_offset_display does the unit conversion off the
live servo ratio, spindle pitch and display factor, and
ui_controller.phase_offset_readout formats it. So the string in the picture is
the string the operator gets, including the fraction naming. The step count fed
in is derived from the live thread pitch, and deliberately sits on an exact
1/N of it: this preview's job is the WIDEST string the strip can be asked to
hold, and the named-fraction branch is the one that has to be seen rendered.
A real groove-widening total is a small decimal and a shorter string.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_phase_offset.py
"""
import os
import tempfile

# Before any kivy/reflex import: isolate the config dir (the app persists widget
# state, and a preview must not edit the developer's saved settings) and force
# the target-hardware size, which Kivy fixes at Window creation.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-phase-preview-")
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

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
MODE_ELS = 2
AXIS_NAMES = ("Z", "X", "S")
IDLE_TICKS = 8
FRACTION_DENOM = 3  # render the strip at an exact 1/3, to exercise the naming
FALLBACK_STEPS = 500

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


def _bar():
    return _find(lambda w: type(w).__name__ == "ElsAdvancedBar")


def settle(n=IDLE_TICKS):
    for _ in range(n):
        EventLoop.idle()


def shot(name):
    out = os.path.join(OUT_DIR, f"phase_offset_{name}.png")
    settle()
    app.root.export_to_png(out)   # first export under-renders; export twice
    app.root.export_to_png(out)
    print("WROTE", out)


def _rows(w, depth=0, out=None):
    if out is None:
        out = []
    wx, wy = w.to_window(w.x, w.y)
    out.append((depth, type(w).__name__, round(w.x), round(w.y), round(w.width),
                round(w.height), round(wy), getattr(w, "size_hint_y", None),
                getattr(w, "text", "")))
    for c in reversed(w.children):
        _rows(c, depth + 1, out)
    return out


def dump(tag):
    """Every rectangle inside the advanced bar, in window coordinates.

    The question this answers is not "does it fit" -- it is "what is underneath
    it", which is the whole placement argument for a strip that never goes away.
    """
    bar = _bar()
    print(f"\n===== {tag} =====")
    print(f"  bar natural_height={bar.natural_height} height={round(bar.height)} "
          f"y={round(bar.y)} top={round(bar.top)}")
    print(f"  {'widget':<30} {'x':>5} {'y':>5} {'w':>5} {'h':>5} {'shy':>5}  text")
    for d, name, x, y, w, h, wy, shy, text in _rows(bar):
        text = (text or "").replace("\n", " ")[:34]
        print(f"  {'  ' * d}{name:<{30 - 2 * d}} {x:>5} {y:>5} {w:>5} {h:>5} "
              f"{str(shy):>5}  {text}")
    # The 2026-08-16 defect, asserted rather than eyeballed: a strip whose
    # position is not accounted for renders OUTSIDE the bar, up over the DRO
    # rows. Widget coordinates here are already window coordinates, so the
    # comparison is directly against the bar's own edges.
    for c in reversed(bar.children):
        for gc in reversed(c.children):
            if round(gc.top) > round(bar.top) + 1 or round(gc.y) < round(bar.y) - 1:
                print(f"  !! OUTSIDE THE BAR: {type(gc).__name__} "
                      f"y={round(gc.y)} top={round(gc.top)} "
                      f"(bar {round(bar.y)}..{round(bar.top)})")
    # And the two overlays must never occupy the same pixels.
    notice = bar.ids.notice_overlay
    status = bar.ids.status_overlay
    if notice.height and status.height:
        if status.top > notice.y + 1:
            print(f"  !! OVERLAP: status {round(status.y)}..{round(status.top)} "
                  f"vs notice {round(notice.y)}..{round(notice.top)}")
    print(f"  notice_overlay y={round(notice.y)} h={round(notice.height)}   "
          f"status_overlay y={round(status.y)} h={round(status.height)}")


def set_offset(fraction_of_pitch):
    """Drive the REAL poller with a stubbed step read.

    Everything downstream -- the unit conversion, the fraction naming, the
    string -- is production code running against the app's live geometry. Two
    polls because the poller deliberately renders a total only on its second
    consecutive sighting (torn 32-bit Modbus reads).

    The stub is on `tick.phase_offset_steps`, the ONCE-PER-TICK SNAPSHOT read
    the poller actually makes since the Modbus-collapse change (0fb8f13) --
    not on `read_phase_offset_steps`, the live reader it made before. From
    that commit until 2026-08-25 this function stubbed the old reader, so the
    poller went to the real, disconnected HAL, counted a fabricated zero, and
    discarded every poll: the strip NEVER LIT in this preview, every _on
    screenshot was quietly identical to its _off twin, and the nothing-moved
    check printed PASS over a comparison of two identical layouts. A preview
    whose subject cannot appear verifies nothing.
    """
    uic = app.els_uic
    fsm = uic._els_fsm
    if fraction_of_pitch is None:
        steps = 0
    else:
        pitch = fsm.thread_pitch_steps()
        steps = int(round(pitch * fraction_of_pitch)) if pitch > 0 else FALLBACK_STEPS
        print(f"  thread pitch = {pitch:.1f} steps -> offset {steps} steps "
              f"({fraction_of_pitch:.4f} x pitch)")
    uic._hal.tick.phase_offset_steps = lambda: steps
    uic._poll_phase_offset()
    uic._poll_phase_offset()
    print(f"  active={uic.phase_offset_active!r} text={uic.phase_offset_text!r}")
    assert uic.phase_offset_active == (fraction_of_pitch is not None), (
        "the poller did not take the stubbed total -- the strip under preview "
        "is not showing what this script is about to caption it as showing")


def set_latched(on):
    """Light (or douse) the thread-reference latch lamp through its poller.

    Same shape as set_offset: stub the tick-snapshot accessors, run the real
    poller. Both terms stubbed together because the lamp is latched AND
    enabled by definition -- and one poll is enough, the two registers are
    uint16 and the poller deliberately has no seen-twice guard.
    """
    uic = app.els_uic
    uic._hal.tick.reference_latched = lambda: on
    uic._hal.tick.enable = lambda: on
    uic._poll_thread_ref_latched()
    print(f"  thread_ref_latched={uic.thread_ref_latched!r}")
    assert uic.thread_ref_latched == on, "the lamp poller did not take the stub"


def _sizes():
    """Every widget on the ELS screen, by identity, with its size.

    The whole reason the strips are overlays is Evan's 2026-08-22 note that
    "having things resize around a temporary warning is distracting", and a
    persistent strip makes that a permanent distortion rather than a blink. So
    the claim is checked rather than eyeballed: same widget objects, same
    rectangles, offset off and on.
    """
    return {id(w): (type(w).__name__, round(w.width), round(w.height),
                    round(w.x), round(w.y))
            for w in _walk(app.root)}


def _walk(w, out=None):
    if out is None:
        out = []
    out.append(w)
    for c in w.children:
        _walk(c, out)
    return out


def assert_nothing_moved(before, after, tag, exempt):
    """Nothing but the strip itself may change size OR position."""
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


def _capture(_dt):
    bar = _bar()
    # The strip and its own label are exempt: they are what appears. `.__self__`
    # because kv ids hold WeakProxy wrappers, and id(proxy) != id(widget) -- the
    # first version of this exemption matched nothing and the check "failed" on
    # the two widgets it was meant to excuse.
    exempt = {id(bar.ids.status_overlay.__self__),
              id(bar.ids.label_phase_offset.__self__),
              # The thread-ref latch lamp shares the band (2026-08-24): it is
              # part of what appears, not part of what must hold still.
              id(bar.ids.label_ref_latched.__self__)}

    def variants(tag):
        set_offset(None)
        set_latched(False)
        app.els_uic.takeup_warning = ""
        settle()
        dump(f"{tag} / no offset")
        shot(f"{tag}_off")
        before = _sizes()

        set_offset(1.0 / FRACTION_DENOM)
        settle()
        dump(f"{tag} / offset 1-3 pitch")
        assert_nothing_moved(before, _sizes(), f"{tag} offset on", exempt)
        shot(f"{tag}_on")

        # BOTH AT ONCE is the case the placement has to survive: a transient
        # refusal landing while a persistent offset is up. They must not
        # overlap each other, and neither may resize anything.
        app.els_uic.takeup_warning = WARNING
        settle()
        dump(f"{tag} / offset + takeup warning")
        shot(f"{tag}_on_plus_warning")
        app.els_uic.takeup_warning = ""

        # The latch lamp's two looks. ALONE is the one the width expression
        # exists for -- the band must shrink to lamp width and cover one
        # header, not park an 800 px accent band over all four to say three
        # words. With the offset it shares the full band.
        set_offset(None)
        set_latched(True)
        settle()
        dump(f"{tag} / latch lamp alone")
        assert_nothing_moved(before, _sizes(), f"{tag} lamp on", exempt)
        shot(f"{tag}_lamp")

        set_offset(1.0 / FRACTION_DENOM)
        settle()
        dump(f"{tag} / latch lamp + offset")
        assert_nothing_moved(before, _sizes(), f"{tag} lamp + offset", exempt)
        shot(f"{tag}_lamp_plus_offset")
        set_latched(False)

    try:
        # Stop-only first: it is the mode the machine is actually run in, and
        # the one whose control row has no wizard strip to spend.
        variants("stoponly")

        bar.enable_wizard = True
        bar.enable_retract = True
        settle()
        variants("wizard")
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

    # REAL MACHINE GEOMETRY, because the readout is arithmetic over it and a
    # fresh config's placeholder ratio (400/360, i.e. 1.1 mm per leadscrew
    # STEP) renders a one-step offset as "+1.111 mm, 0.309 x pitch" -- a
    # true picture of a machine nobody owns. A 5 mm leadscrew at 2000 steps/rev
    # is an ordinary conversion, and it goes in through the production setter
    # so servo.ratioNum/Den are derived exactly the way the app derives them.
    # elsMode first: ServoDispatcher.configure_lead_screw_ratio returns
    # immediately unless it is set, so the three assignments below would land in
    # their properties and change nothing (they did, on the first run of this
    # preview -- the readout stayed on the placeholder ratio).
    app.servo.elsMode = True
    app.servo.leadScrewPitchIn = False
    app.servo.leadScrewPitch = 5
    app.servo.leadScrewPitchSteps = 2000
    # 1.50 mm pitch from the real THREAD_MM table: 600 leadscrew steps to the
    # pitch, so the exact 1/3 rendered here is 200 steps / 0.500 mm and the strip
    # is showing a job that could be run.
    els_bar.set_feed_ratio("Thread MM", 8)

    def _stoponly(_d):
        # AFTER the mode swap mounts the bar -- setting it in _arm silently does
        # nothing (preview_banner_placements.py learned this the hard way).
        adv = _bar()
        if adv is not None:
            adv.enable_wizard = False
            adv.enable_retract = False
        Clock.schedule_once(_capture, 1.0)

    Clock.schedule_once(_stoponly, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
