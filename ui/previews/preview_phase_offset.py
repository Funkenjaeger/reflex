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
# By character count, not named by key: the point is to render whichever
# message is currently the longest, and a key pinned here would go stale the
# moment the texts are edited again.
WIDEST_WARNING = max(ELS_TAKEUP_MESSAGES.values(), key=len)

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
    # THE GUTTER MUST BE THERE, IN EVERY MODE. It is the whole point of the
    # 2026-08-29 change: the persistent chips are printed in reserved space
    # rather than overlaid on the field headers, so a zero-height gutter means
    # both of them rendered nowhere while the controller reported them shown.
    gutter = bar.ids.status_gutter
    print(f"  status_gutter y={round(gutter.y)} h={round(gutter.height)}")
    if not gutter.height:
        print("  !! GUTTER COLLAPSED -- both status chips are rendered nowhere")

    # And each chip must sit inside it, pinned to its own edge.
    for name in ("chip_reference", "chip_phase"):
        chip = bar.ids[name]
        print(f"  {name:<16} x={round(chip.x)} y={round(chip.y)} "
              f"w={round(chip.width)} h={round(chip.height)} "
              f"opacity={chip.opacity:.0f} lit={getattr(chip, 'lit', None)} "
              f"text={chip.text!r} value={chip.value!r}")
        if round(chip.y) < round(gutter.y) - 1 or round(chip.top) > round(gutter.top) + 1:
            print(f"  !! {name} IS OUTSIDE THE GUTTER: "
                  f"{round(chip.y)}..{round(chip.top)} vs "
                  f"{round(gutter.y)}..{round(gutter.top)}")

    # NOTHING PERMANENT MAY COVER THE FIELD HEADERS any more. The only overlay
    # left is the transient one; with no notice up its height is 0, so the
    # "Stop Z" / "Major ø" headers are clear whenever nothing is being said.
    notice = bar.ids.notice_overlay
    print(f"  notice_overlay y={round(notice.y)} h={round(notice.height)}")
    for hdr in ("btn_stop_z", "btn_major_dia"):
        b = bar.ids[hdr]
        covered = notice.height and notice.y < b.top and notice.top > b.y
        print(f"  {hdr:<16} y={round(b.y)}..{round(b.top)}  "
              f"{'COVERED by notice_overlay' if covered else 'clear'}")


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


def measure_takeup_texts():
    """Every take-up warning's RENDERED width against the gap between chips.

    THE CONSTRAINT IS PIXELS, NOT CHARACTERS. The notice strip is translucent
    and pinned across the top of the bar -- over the status gutter -- so a
    message wider than the space between chip_reference's right edge and
    chip_phase's left edge draws on top of the phase chip's text and both
    become unreadable. Evan accepts the chips being dimmed by the red tint; he
    does not accept text on text.

    Measured off a real texture in the same face and size the strip uses
    (theme.font_bold at dp(13)), not estimated from a character count: the
    character budget in tests/fsms/test_els_cal.py is a CI-cheap proxy, and
    this is what calibrates it. The strip's own Label cannot be measured
    directly -- it sets `text_size: self.size`, so its texture is the full
    917 px band regardless of the string.
    """
    from kivy.core.text import Label as CoreLabel
    from kivy.metrics import dp
    from reflex.utils.devices import (ELS_TAKEUP_MESSAGES,
                                      ELS_TAKEUP_WRONG_WAY,
                                      ELS_TAKEUP_UNKNOWN)

    from reflex.fsms.ui_controller import phase_offset_chip_readout

    bar = _bar()
    theme = app.theme
    float_ = bar.ids.bar_float
    left = bar.ids.chip_reference.right
    gap = bar.ids.chip_phase.x - left

    # THE GAP IS NOT THE BUDGET, and this function said it was until the
    # render disagreed. The strip's Label is halign 'center' across the FULL
    # bar, and the gap is not centred on the bar (197..783 against a bar
    # centre of 566), so a centred string may only reach as far as the NEARER
    # obstruction -- and then the same distance the other way. Comparing
    # against the gap reported every message as fitting while the widest one
    # rendered on top of the phase chip.
    centre = float_.center_x
    budget = 2 * min(centre - left, bar.ids.chip_phase.x - centre)

    # THE GAP IS NOT CONSTANT, and the narrow case is the one that matters.
    # The phase chip sizes to its own value, and its LONGEST value is not the
    # ordinary "+0.500 mm  0.333 x pitch" -- it is the un-convertible readout
    # the poller falls back to when the thread geometry is missing, which is
    # deliberately verbose because it must not read as "no offset". Built by
    # production code rather than typed here, so it cannot drift from what the
    # chip would really show.
    saved = app.els_uic.phase_offset_chip_value
    worst_value = phase_offset_chip_readout(
        500, 0.0, 0.0, getattr(app.els_uic._board, "formats", None))
    app.els_uic.phase_offset_chip_value = worst_value
    settle()
    narrow_gap = bar.ids.chip_phase.x - bar.ids.chip_reference.right
    narrow_budget = 2 * min(centre - bar.ids.chip_reference.right,
                            bar.ids.chip_phase.x - centre)
    app.els_uic.phase_offset_chip_value = saved
    settle()

    texts = {str(k): v for k, v in ELS_TAKEUP_MESSAGES.items()}
    texts["WRONG_WAY"] = ELS_TAKEUP_WRONG_WAY
    texts["UNKNOWN"] = ELS_TAKEUP_UNKNOWN

    print("\n===== take-up warning widths vs the space a CENTRED string has =====")
    print(f"  chip_reference.right={round(left)}  chip_phase.x="
          f"{round(bar.ids.chip_phase.x)}  bar centre={round(centre)}")
    print(f"  raw gap {round(gap)} px, but a centred string gets "
          f"{round(budget)} px")
    print(f"  worst-case phase value {worst_value!r}")
    print(f"  -> narrow gap {round(narrow_gap)} px, centred budget "
          f"{round(narrow_budget)} px")
    worst = 0
    worst_key = None
    for key, msg in texts.items():
        cl = CoreLabel(text=msg, font_name=theme.font_bold, font_size=dp(13))
        cl.refresh()
        w = cl.texture.size[0]
        if w > worst:
            worst, worst_key = w, key
        verdict = ("fits" if w <= budget else "!! OVERFLOWS")
        narrow = ("fits" if w <= narrow_budget else "!! OVERFLOWS")
        print(f"  {key:<12} {len(msg):>3} chars  {round(w):>4} px  "
              f"{w / max(len(msg), 1):.2f} px/char   "
              f"normal: {verdict:<12} worst-case phase chip: {narrow}")
    print(f"  LONGEST: {worst_key} at {round(worst)} px -- "
          f"{round(budget - worst):+d} px against the normal budget, "
          f"{round(narrow_budget - worst):+d} px against the worst-case one")
    return worst, budget, narrow_budget


def set_threading(on):
    """Switch the controller's threading/feed descriptor.

    Assigned directly rather than through els_bar.set_feed_ratio: the feed
    table also sets the thread pitch the offset readout is computed from, and
    changing that here would move the very numbers the shots are comparing.
    ElsAdvancedBar._sync_is_threading only writes this on an els_bar mode
    change, so a direct assignment stays put.
    """
    app.els_uic.is_threading = on
    print(f"  is_threading={app.els_uic.is_threading!r}")


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
    # The two chips are exempt: they are what appears, and each sizes to its own
    # text, so their widths move by design. Their CHILD labels go with them --
    # the chip's width is the sum of those two textures, so exempting only the
    # chip would fail on the labels inside it. `.__self__` because kv ids hold
    # WeakProxy wrappers, and id(proxy) != id(widget) -- the first version of
    # this exemption matched nothing and the check "failed" on the two widgets
    # it was meant to excuse.
    exempt = set()
    for name in ("chip_reference", "chip_phase"):
        for w in _walk(bar.ids[name].__self__):
            exempt.add(id(w))

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
        measure_takeup_texts()
        app.els_uic.takeup_warning = WARNING
        settle()
        dump(f"{tag} / offset + takeup warning")
        shot(f"{tag}_on_plus_warning")

        # AND THE WIDEST ONE, which is the case the 2026-08-29 rewrite was
        # for. The notice strip is translucent and sits over the gutter, so
        # the question is not "does the common message fit" but "does the
        # worst one land on the phase chip's text".
        app.els_uic.takeup_warning = WIDEST_WARNING
        settle()
        shot(f"{tag}_on_plus_widest_warning")
        app.els_uic.takeup_warning = ""

        # The reference chip's three looks. It is ALWAYS PRESENT, so the
        # unreferenced one is already in the shots above; these are the other
        # two.
        set_offset(None)
        set_latched(True)
        settle()
        dump(f"{tag} / reference latched")
        assert_nothing_moved(before, _sizes(), f"{tag} lamp on", exempt)
        shot(f"{tag}_lamp")

        set_offset(1.0 / FRACTION_DENOM)
        settle()
        dump(f"{tag} / reference latched + offset")
        assert_nothing_moved(before, _sizes(), f"{tag} lamp + offset", exempt)
        shot(f"{tag}_lamp_plus_offset")

        # THE THIRD STATE, and the reason the chip is not a lamp. In FEED mode
        # the reference is still LATCHED -- the firmware clears
        # referenceLatched only on an elsStop.enable 0->1 edge and a mode
        # switch never writes enable -- it is just not being used right now.
        # So the chip DARKENS rather than disappearing: the cue the operator
        # needs is that his phase reference survives a turn-feed-turn swap.
        # This shot is the one that has to be looked at, not reasoned about.
        set_threading(False)
        settle()
        dump(f"{tag} / reference latched, FEED mode (not relevant)")
        assert_nothing_moved(before, _sizes(), f"{tag} feed mode", exempt)
        shot(f"{tag}_lamp_feed_mode")
        set_threading(True)

        set_offset(None)
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
