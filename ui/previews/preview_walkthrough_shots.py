"""Headless PNG set for the two threading features' operator surfaces.

WHY THIS EXISTS. The UI/UX walkthrough needs every state of the pick-up-
existing-thread wizard and the thread-phase offset modal side by side, at the
machine's real 1024x600, with the REAL widget tree producing the pixels. Same
contract as previews/preview_phase_offset.py, preview_takeup_banner.py and
preview_status_notice.py: nothing on these screens is typed in here. Every
sentence comes out of production code (ThreadResync.message, REFUSAL_TEXT,
JOG_TEXT/ALIGN_TEXT/..., ElsFsm.phase_offset_display), and every number is
arithmetic the app did over the app's own live geometry.

WHY Window.screenshot AND NOT export_to_png. A Popup is a ModalView parented
to Window, not to app.root, so `app.root.export_to_png()` -- the capture the
three previews above use -- renders the screen with the modal MISSING.
previews/preview_widgets.py records that "Window.screenshot returns black on
this headless GL"; re-probed 2026-08-23 under the same
`xvfb-run -s "-screen 0 1024x600x24"` the other previews run in, it is not
black, and it is the only path that captures the dimmed backdrop and the modal
in one frame. Window.screenshot() inserts a counter into the filename, so each
shot is captured to a scratch name and moved onto the exact one asked for.

WHERE THE HARDWARE BOUNDARY IS STUBBED, AND ONLY THERE.
  * Wizard: a stub standing in for ElsStopHal, plus the two encoder-count
    callables ThreadResync already takes injected ("so this class can be driven
    in tests without a running app" -- its own docstring). The ThreadResync
    policy, the tolerance, every state transition and every message above that
    line are production.
  * Phase offset: four read/write methods shadowed on the REAL ElsStopHal
    instance (read_phase_offset_steps / _seq, read_enable, request_phase_offset)
    and board.connected forced. ElsFsm.apply_phase_offset then runs for real --
    the AT_PITCH, NEGATIVE and NO_JOB refusals in this set are all produced by
    the FSM refusing an actual entry, not selected from the message table.
  * Strip: the same stubbed step read previews/preview_phase_offset.py uses.

IT IS ALSO THE REGRESSION CHECK FOR SIX LAYOUT/SEVERITY DEFECTS fixed
2026-08-23, found by rendering these same two modals side by side:
    D1  the wizard's body overflowed its fixed box and drew over its own title
    D2  "Fill entry:" was clipped to a half-cut "Fill" over "entr"
    D3  four of the eight phase-offset messages hid their last, actionable
        sentence below the fold of a 92 px scroller
    D4  the wizard rendered a "Do not cut" custody fault in the same neutral
        grey as its routine instructions
    D5  the wizard's not-connected refusal was four words with no next step
    D6  the live readout was the dimmest thing on the screen where it mattered
Each has assertions here, made against the real widget tree at the real
1024x600, and the run ends in a PASS/FAIL table. "The code changed" is not
evidence a layout defect is fixed; the pixels are. Seen-red 2026-08-23: each
fix reverted one at a time reddens its own defect and no other.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_walkthrough_shots.py
"""
import os
import tempfile

# Before any kivy/reflex import: isolate the config dir (the app persists widget
# state, and a preview must not edit the developer's saved settings) and force
# the target-hardware size, which Kivy fixes at Window creation.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-walkthrough-")
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
from kivy.core.window import Window  # noqa: E402
from kivy.uix.scrollview import ScrollView  # noqa: E402

from reflex.app import MainApp  # noqa: E402
from reflex.fsms.els_fsm import ElsFsm  # noqa: E402
from reflex.fsms.els_resync import ThreadResync  # noqa: E402
from reflex.utils.devices import ELS_PROTOCOL_VERSION  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
SCRATCH = os.path.join(OUT_DIR, "_wt_scratch.png")

MODE_ELS = 2
AXIS_NAMES = ("Z", "X", "S")
STARTS = 3                 # a 3-start thread: the offset is one third of a pitch
Z_BASELINE = 12000         # arbitrary but realistic raw encoder counts
SPINDLE_BASELINE = 8192

app = MainApp()

# Everything printed for the write-up, collected as it is rendered rather than
# transcribed afterwards.
TRANSCRIPT = []


# ── plumbing ─────────────────────────────────────────────────────────
def settle(n=14):
    for _ in range(n):
        EventLoop.idle()


def shot(name, note=""):
    """One 1024x600 frame, including any modal, at the exact filename asked for."""
    settle()
    target = os.path.join(OUT_DIR, f"{name}.png")
    for path in (SCRATCH, target):
        if os.path.exists(path):
            os.remove(path)
    # Two passes: the first guarantees a fully-drawn frame sits in BOTH buffers
    # of the double-buffered surface before glReadPixels is asked for one.
    got = Window.screenshot(name=SCRATCH)
    if got and os.path.exists(got):
        os.remove(got)
    settle(4)
    got = Window.screenshot(name=SCRATCH)
    os.replace(got, target)
    size = os.path.getsize(target)
    print(f"WROTE {target}  ({size} bytes){('  # ' + note) if note else ''}")


def walk(w, out=None):
    if out is None:
        out = []
    out.append(w)
    for c in w.children:
        walk(c, out)
    return out


def find(pred, root=None):
    for w in walk(root if root is not None else app.root):
        if pred(w):
            return w
    return None


def adv_bar():
    return find(lambda w: type(w).__name__ == "ElsAdvancedBar")


def record(title, **fields):
    TRANSCRIPT.append((title, fields))
    print(f"\n----- TEXT: {title} -----")
    for key, value in fields.items():
        print(f"  {key}: {value!r}")


# Whatever a picture shows, the amount by which it is wrong is a number.
#
# Both modals are variable-length production prose in a box the machine's
# 600 px screen puts a hard ceiling on, and until 2026-08-23 they failed in
# OPPOSITE directions: the wizard's body was sized to its own texture inside a
# fixed BoxLayout, so a body one line too tall was drawn upward OVER the
# popup's own title; the offset modal's message was scrolled, which kept it off
# the buttons and hid the actionable last sentence of four of its eight
# messages behind a 2 px hairline instead.
#
# Both are now self-sizing AND scrolled, so both defects are measured the same
# way every run, and the measurement is an ASSERTION rather than a printout: a
# regression on any of the six fixes below fails this script loudly instead of
# needing someone to notice it in a PNG.
MEASUREMENTS = []
CHECKS = []


def check(defect, tag, ok, detail):
    CHECKS.append((defect, tag, bool(ok), detail))
    print(f"  {'PASS' if ok else '**FAIL**'} [{defect} {tag}] {detail}")
    return bool(ok)


def line_height(label):
    try:
        return float(label._label.get_extents("Mg")[1]) or 22.0
    except Exception:
        return 22.0


def measure_fit(tag, popup, label_id, scroller_id, defect):
    """How much of the text is on screen, and whether any of it escaped.

    Two independent failures, checked separately because the old fixes traded
    one for the other:
      * CONTAINED -- the text lives inside a widget that CLIPS, and that widget
        is inside the popup's content box. This is what stops a body from being
        drawn over the title, and it is structural: it holds for a body of any
        length, which the old `height: self.texture_size[1]` did not.
      * WHOLE -- none of it is below the fold. This is what the popup's derived
        height buys, and it is the half a scroller alone does not give you.
    """
    box = popup.content
    label = popup.ids[label_id]
    sv = popup.ids[scroller_id]
    line = line_height(label)

    hidden = round(label.height - sv.height)
    row = (tag, f"texture h={round(label.height)}  viewport h={round(sv.height)}  "
                f"popup h={round(popup.height)}  -> "
                + (f"TRUNCATED: {hidden} px (~{hidden / line:0.1f} lines) below "
                   f"the fold" if hidden > 1 else "fits whole"))
    MEASUREMENTS.append(row)
    print(f"  FIT [{row[0]}] {row[1]}")

    check(defect, f"{tag}/clipped",
          isinstance(label.parent, ScrollView),
          f"{label_id} is inside a {type(label.parent).__name__} "
          f"(only a clipping parent can stop an over-long body reaching the title)")
    check(defect, f"{tag}/contained",
          sv.top <= box.top + 1 and popup.top <= Window.height + 1,
          f"scroller top {round(sv.top)} <= content-box top {round(box.top)}, "
          f"popup top {round(popup.top)} <= screen {Window.height}")
    check(defect, f"{tag}/whole", hidden <= 1,
          f"{max(hidden, 0)} px (~{max(hidden, 0) / line:0.1f} lines) below the "
          f"fold of a {round(sv.height)} px viewport")


# ── D. the advanced-bar status strip ─────────────────────────────────
def set_strip_offset(fraction_of_pitch):
    """Drive the REAL poller with a stubbed step read.

    Two polls because the poller deliberately renders a total only on its second
    consecutive sighting (torn 32-bit Modbus reads).
    """
    uic = app.els_uic
    fsm = uic._els_fsm
    if fraction_of_pitch is None:
        steps = 0
    else:
        pitch = fsm.thread_pitch_steps()
        steps = int(round(pitch * fraction_of_pitch))
        print(f"  thread pitch = {pitch:.1f} steps -> offset {steps} steps "
              f"({fraction_of_pitch:.4f} of a pitch)")
    uic._hal.read_phase_offset_steps = lambda: steps
    uic._poll_phase_offset()
    uic._poll_phase_offset()
    return steps


def section_strip():
    bar = adv_bar()
    print("\n########## D. advanced-bar status strip ##########")

    set_strip_offset(None)
    app.els_uic.takeup_warning = ""
    settle()
    shot("wt_strip_off", "no offset set")

    set_strip_offset(1.0 / STARTS)
    settle()
    record("advanced-bar status strip",
           phase_offset_text=app.els_uic.phase_offset_text,
           phase_offset_active=app.els_uic.phase_offset_active)
    strip = bar.ids.status_overlay.__self__
    print(f"  strip rect: x={round(strip.x)} w={round(strip.width)} "
          f"y={round(strip.y)} h={round(strip.height)}")
    shot("wt_strip_on", "offset 1/3 of a pitch")

    # ── The REJECTED full-width variant, rebuilt for the comparison figure.
    # els_advbar.kv keeps the strip inset between the Engage card and the mode
    # column and names previews/out/alt_fullwidth_*.png as what full width
    # looked like; that script was never checked in, so the two bindings are
    # overridden here instead. Bound rather than assigned once, so a relayout
    # cannot quietly restore the inset before the frame is captured.
    float_ = bar.ids.bar_float.__self__

    def _full_width(*_a):
        strip.x = float_.x
        strip.width = float_.width

    strip.bind(pos=_full_width, size=_full_width)
    float_.bind(pos=_full_width, size=_full_width)
    _full_width()
    settle()
    print(f"  full-width strip rect: x={round(strip.x)} w={round(strip.width)}")
    shot("wt_strip_fullwidth", "REJECTED full-width variant")

    strip.unbind(pos=_full_width, size=_full_width)
    float_.unbind(pos=_full_width, size=_full_width)
    _full_width  # noqa: B018 - kept referenced for clarity
    set_strip_offset(None)
    settle()


# ── A. the settings popup, the entry point for both features ─────────
def section_settings():
    print("\n########## A. ELS Threading Settings popup ##########")
    from reflex.components.home.els_settings_popup import ElsSettingsPopup

    pop = ElsSettingsPopup(bar=adv_bar())
    pop.open()
    settle(20)

    # Both feature rows are the 7th and 8th of nine in a scroller shorter than
    # its content, i.e. below the fold on open. Scrolled to the bottom so the
    # figure shows them WITH the rows around them, which is what "in context"
    # has to mean on a surface you have to scroll to reach them on.
    grid = pop.ids.grid_layout
    sv = find(lambda w: isinstance(w, ScrollView), pop)
    print(f"  grid height={round(grid.height)} viewport height="
          f"{round(sv.height)}  -> scrollable="
          f"{round(grid.height) > round(sv.height)}")
    sv.scroll_y = 0.0
    settle(20)
    shot("wt_settings_popup", "scrolled to the bottom: both feature rows")
    pop.dismiss()
    settle(12)


# ── B. the pick-up-existing-thread wizard ────────────────────────────
class StubLatchHal:
    """The hardware boundary for ThreadResync, and nothing above it.

    Every method here is one ElsStopHal exposes; the wizard's policy, its
    tolerance, its state machine and all six of its messages are production.
    """

    def __init__(self):
        self.connected = True
        self.enable = True
        self.reference_latched = False
        self.latch_seq = 41
        self.latched_z = Z_BASELINE
        self.latched_spindle = SPINDLE_BASELINE

    def read_protocol_version(self):
        return ELS_PROTOCOL_VERSION

    def read_enable(self):
        return self.enable

    def read_reference_latched(self):
        return self.reference_latched

    def read_latch_seq(self):
        return self.latch_seq

    def request_latch(self):
        pass

    def read_latched_z(self):
        return self.latched_z

    def read_latched_spindle(self):
        return self.latched_spindle


def new_resync_popup(hal, counts):
    from reflex.components.home.els_resync_popup import ThreadResyncPopup

    pop = ThreadResyncPopup()
    pop._resync = ThreadResync(
        hal, app.els,
        read_z_counts=lambda: counts["z"],
        read_spindle_counts=lambda: counts["spindle"],
    )
    pop.open()
    settle(18)
    return pop


def tick_wizard(pop, n):
    for _ in range(n):
        pop._tick(0.0)
    settle(6)


def section_resync():
    print("\n########## B. pick-up-existing-thread wizard ##########")
    from reflex.components.home import els_resync_popup as mod

    # ── 2. jog: the state the wizard opens in.
    hal = StubLatchHal()
    counts = {"z": Z_BASELINE, "spindle": SPINDLE_BASELINE}
    pop = new_resync_popup(hal, counts)
    record("resync / jog", state=pop.state, body=pop.body_text,
           live=pop.live_text, confirm_enabled=pop.confirm_enabled,
           matches_JOG_TEXT=(pop.body_text == mod.JOG_TEXT))
    measure_fit("resync/jog", pop, "lbl_body", "sv_body", "D1")
    shot("wt_resync_jog")

    # ── 3. align: production begin(), then the real stillness dwell.
    pop.begin()
    settle(6)
    tick_wizard(pop, 5)
    record("resync / align (spindle still settling)",
           state=pop.state, live=pop.live_text,
           confirm_enabled=pop.confirm_enabled)
    tick_wizard(pop, ThreadResync.SPINDLE_STILL_POLLS + 5)
    record("resync / align (dwell satisfied)",
           state=pop.state, body=pop.body_text, live=pop.live_text,
           confirm_enabled=pop.confirm_enabled,
           matches_ALIGN_TEXT=(pop.body_text == mod.ALIGN_TEXT))
    measure_fit("resync/align", pop, "lbl_body", "sv_body", "D1")
    shot("wt_resync_align")

    # ── 4. drifted: the carriage creeps through the free play.
    counts["z"] = Z_BASELINE + 11
    tick_wizard(pop, 2)
    record("resync / drifted", state=pop.state, body=pop.body_text,
           live=pop.live_text,
           matches_DRIFTED_TEXT=(pop.body_text == mod.DRIFTED_TEXT))
    measure_fit("resync/drifted", pop, "lbl_body", "sv_body", "D1")
    shot("wt_resync_drifted")

    # ── 7. red_flag: a re-seat that misses the baseline. Taken here, off the
    # live DRIFTED state, because that is the only way in -- reseat_check
    # refuses from any other state.
    counts["z"] = Z_BASELINE - 6
    pop.reseat()
    settle(6)
    record("resync / red_flag", state=pop.state, body=pop.body_text,
           live=pop.live_text)
    measure_fit("resync/red_flag", pop, "lbl_body", "sv_body", "D1")
    shot("wt_resync_red_flag")
    pop.dismiss()
    settle(10)

    # ── 5. latched: a clean run through to the firmware ack.
    hal = StubLatchHal()
    counts = {"z": Z_BASELINE, "spindle": SPINDLE_BASELINE}
    pop = new_resync_popup(hal, counts)
    pop.begin()
    tick_wizard(pop, ThreadResync.SPINDLE_STILL_POLLS + 5)
    pop.confirm()
    hal.reference_latched = True
    hal.latch_seq += 1                     # the firmware's ack
    tick_wizard(pop, 2)
    record("resync / latched", state=pop.state, body=pop.body_text,
           live=pop.live_text)
    measure_fit("resync/latched", pop, "lbl_body", "sv_body", "D1")
    shot("wt_resync_latched")
    pop.dismiss()
    settle(10)

    # ── 6. refused: no job armed, the refusal an operator actually meets.
    hal = StubLatchHal()
    hal.enable = False
    counts = {"z": Z_BASELINE, "spindle": SPINDLE_BASELINE}
    pop = new_resync_popup(hal, counts)
    pop.begin()
    settle(6)
    record("resync / refused (no job armed)", state=pop.state,
           body=pop.body_text)
    measure_fit("resync/refused", pop, "lbl_body", "sv_body", "D1")
    shot("wt_resync_refused")
    pop.dismiss()
    settle(10)

    # The other refusals this same surface renders, for the write-up. Driven
    # through the same production entry point, not read out of the source.
    for label, setup in (
        ("not connected", lambda h: setattr(h, "connected", False)),
        ("job already has a reference",
         lambda h: setattr(h, "reference_latched", True)),
        ("no Z or spindle axis assigned", None),
    ):
        probe = StubLatchHal()
        cnt = {"z": Z_BASELINE, "spindle": SPINDLE_BASELINE}
        p = mod.ThreadResyncPopup()
        if setup is None:
            p._resync = None
        else:
            setup(probe)
            p._resync = ThreadResync(probe, app.els,
                                     read_z_counts=lambda: cnt["z"],
                                     read_spindle_counts=lambda: cnt["spindle"])
        p.begin()
        record(f"resync / refused ({label}) [not rendered]",
               state=p.state, body=p.body_text)

    # And the version refusal, which needs a version that is not the real one.
    probe = StubLatchHal()
    probe.read_protocol_version = lambda: ELS_PROTOCOL_VERSION - 1
    cnt = {"z": Z_BASELINE, "spindle": SPINDLE_BASELINE}
    p = mod.ThreadResyncPopup()
    p._resync = ThreadResync(probe, app.els,
                             read_z_counts=lambda: cnt["z"],
                             read_spindle_counts=lambda: cnt["spindle"])
    p.begin()
    record("resync / refused (firmware too old) [not rendered]",
           state=p.state, body=p.body_text)


# ── C. the thread phase offset modal ─────────────────────────────────
def section_phase_offset():
    print("\n########## C. thread phase offset modal ##########")
    from reflex.components.home.els_phase_offset_popup import (
        PhaseOffsetPopup, REFUSAL_TEXT)

    hal = app.els_uic.hal
    fsm = app.els_uic.els_fsm

    # The FSM asks the BOARD whether it is connected, so the board is the thing
    # that has to say yes. Its connect handler is unbound first: it would
    # reconcile retained firmware state against a board that is not there.
    app.board.unbind(connected=app.els_uic._on_connected_changed)
    app.board.connected = True

    ctl = {"steps": 0, "seq": 500, "enable": True, "written": None}
    hal.read_phase_offset_steps = lambda: ctl["steps"]
    hal.read_phase_offset_seq = lambda: ctl["seq"]
    hal.read_enable = lambda: ctl["enable"]
    hal.request_phase_offset = lambda total: ctl.update(written=int(total))

    def apply_linked(popup):
        """Press Apply with the link up.

        Board's own poll loop sets `connected = False` whenever a read fails,
        and with no board every read fails -- so a value set once at the top of
        this section is False again a few frames later. The first run of this
        preview rendered the NEGATIVE and NO_JOB shots with the OFFLINE
        refusal for exactly that reason: `_phase_offset_blockers` checks
        connectivity FIRST, so an unnoticed disconnect masks every other
        refusal behind it. Re-asserted immediately before the synchronous call
        that reads it.
        """
        app.board.connected = True
        popup.apply()
        assert popup.message != REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_OFFLINE], (
            "the link dropped again before apply() read it")

    pitch = fsm.thread_pitch_steps()
    third = int(round(pitch / STARTS))
    print(f"  is_threading={app.els_uic.is_threading}  "
          f"thread pitch={pitch:.1f} steps  1/3 = {third} steps")

    pop = PhaseOffsetPopup()
    pop.open()
    settle(20)
    msg_sv = pop.ids["sv_message"]

    # ── 8. entry, total zero.
    fill_label = None
    for w in walk(pop):
        if getattr(w, "text", "") == "Fill entry:":
            fill_label = w
    if fill_label is not None:
        # Compared against the label's TEXT area, not its widget width: the
        # caption carries padding: dp(16), 0, so 32 px of the allotted width
        # was never available to the string. The original measurement here
        # compared against the widget width and still called it a clip -- it
        # was UNDERSTATING the shortfall by a full 32 px.
        #
        # The width comparison holds BY CONSTRUCTION while the fix is in place
        # (width is bound to texture_size, which is text + padding), so the
        # load-bearing half of this check is the one-line test: a caption that
        # goes back to a fraction of the row wraps, and a wrapped texture is
        # two line-heights tall in a row that has room for one.
        pad = fill_label.padding
        pad = (pad[0] + pad[2]) if len(pad) >= 4 else 2 * pad[0]
        nat = fill_label._label.get_extents(fill_label.text)[0]
        room = fill_label.width - pad
        one_line = round(fill_label.texture_size[1]) <= round(line_height(fill_label) + 2)
        fits = "fits on one line" if nat <= room and one_line else "WRAPS AND CLIPS"
        detail = (f'"Fill entry:" natural width {round(nat)} px vs '
                  f'{round(room)} px of text area ({round(fill_label.width)} px '
                  f'wide less {round(pad)} px padding); texture '
                  f'{round(fill_label.texture_size[1])} px vs one line at '
                  f'{round(line_height(fill_label))} px -> {fits}')
        MEASUREMENTS.append(("offset/fill-label", detail))
        print(f"  FIT [offset/fill-label] {detail}")
        check("D2", "offset/fill-label", nat <= room and one_line, detail)
    else:
        check("D2", "offset/fill-label", False,
              'no widget with text "Fill entry:" -- has the row changed shape?')
    record("offset / entry, total zero", state=pop.state,
           total=pop.total_text, entry_text=pop.entry_text,
           message=pop.message)
    measure_fit("offset/entry", pop, "lbl_message", "sv_message", "D3")
    shot("wt_offset_entry_zero")

    # ── 9. entry, nonzero total. The total is the firmware's; the entry is
    # filled by the production 1/3-pitch button.
    ctl["steps"] = third
    pop._refresh_total()
    pop.fill_fraction(STARTS)
    settle(6)
    record("offset / entry, total 1/3 of a pitch", state=pop.state,
           total=pop.total_text, entry_text=pop.entry_text,
           message=pop.message)
    shot("wt_offset_entry_total")

    # ── 10. waiting for the ack. The modal's own tick would time this out
    # after ACK_TIMEOUT_POLLS, so the poll counter is held at zero while the
    # frame is captured -- the state is real, the clock is not allowed to run.
    apply_linked(pop)
    print(f"  apply -> state={pop.state} busy={pop.busy} "
          f"wrote total={ctl['written']} steps")
    for _ in range(24):
        pop._ack_polls = 0
        EventLoop.idle()
    pop._ack_polls = 0
    record("offset / waiting", state=pop.state, total=pop.total_text,
           message=pop.message, busy=pop.busy)
    _shot_frozen(pop, "wt_offset_waiting")

    # ── 11. applied: the firmware acks and the new total lands.
    ctl["steps"] = ctl["written"]
    ctl["seq"] += 1
    pop._tick(0.0)
    settle(6)
    record("offset / applied", state=pop.state, total=pop.total_text,
           message=pop.message, busy=pop.busy)
    measure_fit("offset/applied", pop, "lbl_message", "sv_message", "D3")
    shot("wt_offset_applied")

    # ── 12. AT_PITCH: another third on top of two thirds. Refused by
    # ElsFsm.apply_phase_offset for real, not selected from the table.
    pop.entry = float(fsm.steps_to_display(third))
    apply_linked(pop)
    settle(6)
    print(f"  apply {pop.entry} on a total of {ctl['steps']}/{pitch:.0f} "
          f"steps -> {pop.state}")
    record("offset / refused AT_PITCH", state=pop.state,
           total=pop.total_text, entry_text=pop.entry_text,
           message=pop.message)
    measure_fit("offset/AT_PITCH", pop, "lbl_message", "sv_message", "D3")
    shot("wt_offset_refused_at_pitch")

    # ── 13. NEGATIVE. The keypad has a sign key (keypad.py sign_key), so a
    # minus really can be typed into this field.
    ctl["steps"] = third
    pop._refresh_total()
    pop.entry = -float(fsm.steps_to_display(third))
    apply_linked(pop)
    settle(6)
    record("offset / refused NEGATIVE", state=pop.state,
           total=pop.total_text, entry_text=pop.entry_text,
           message=pop.message)
    measure_fit("offset/NEGATIVE", pop, "lbl_message", "sv_message", "D3")
    shot("wt_offset_refused_negative")

    # ── 14. NO_JOB. The firmware clears the total on the enable edge, so a
    # disengaged job shows a zero total -- which is the whole reason this
    # refusal has to be a sentence rather than a number that did not move.
    ctl["enable"] = False
    ctl["steps"] = 0
    pop._refresh_total()
    pop.entry = float(fsm.steps_to_display(third))
    apply_linked(pop)
    settle(6)
    record("offset / refused NO_JOB", state=pop.state,
           total=pop.total_text, entry_text=pop.entry_text,
           message=pop.message)
    measure_fit("offset/NO_JOB", pop, "lbl_message", "sv_message", "D3")
    shot("wt_offset_refused_no_job")

    # ── Every message this modal can put in that scroller, measured. This is
    # the whole point of the exercise: four of these eight used to be taller
    # than the viewport, and the part below the fold was in every case the LAST
    # sentence -- the one that says what to do. Driven through _show(), the same
    # call every real refusal goes through.
    from reflex.components.home.els_phase_offset_popup import (
        CLEARED_TEXT, MESSAGE_CHAR_BUDGET, NO_ACK_TEXT)
    print("\n  ---- every message vs the message viewport ----")
    catalogue = [(f"REFUSAL {code}", text) for code, text in REFUSAL_TEXT.items()]
    catalogue += [("NO_ACK", NO_ACK_TEXT), ("CLEARED", CLEARED_TEXT)]
    for label, text in catalogue:
        pop._show(text, state="refused")
        settle(4)
        lbl = pop.ids["lbl_message"]
        line = line_height(lbl)
        hidden = round(lbl.height - msg_sv.height)
        verdict = (f"{hidden} px hidden (~{hidden / line:0.1f} lines)"
                   if hidden > 1 else "fits")
        detail = (f"{len(text)} chars (budget {MESSAGE_CHAR_BUDGET}), popup "
                  f"{round(pop.height)} px, viewport {round(msg_sv.height)} px, "
                  f"texture {round(lbl.height)} px -> {verdict}")
        MEASUREMENTS.append((f"offset/{label}", detail))
        print(f"  FIT [offset/{label}] {detail}")
        check("D3", f"offset/{label}", hidden <= 1, detail)
        check("D3", f"offset/{label}/budget", len(text) <= MESSAGE_CHAR_BUDGET,
              f"{len(text)} chars vs a {MESSAGE_CHAR_BUDGET}-char budget")

    # And every wizard body, against ITS box.
    from reflex.components.home import els_resync_popup as rmod
    print("\n  ---- every wizard body vs its content box ----")
    probe_pop = rmod.ThreadResyncPopup()
    probe_pop.open()
    settle(16)
    wizard_texts = [
        ("JOG_TEXT", rmod.JOG_TEXT),
        ("ALIGN_TEXT", rmod.ALIGN_TEXT),
        ("DRIFTED_TEXT", rmod.DRIFTED_TEXT),
        ("latched + AIR_PASS_TEXT",
         "Thread reference latched (spindle 8192, Z 12000)." + rmod.AIR_PASS_TEXT),
    ]
    for label, text in wizard_texts:
        probe_pop.body_text = text
        settle(4)
        measure_fit(f"resync/{label}", probe_pop, "lbl_body", "sv_body", "D1")

    # ── The residual case, exercised rather than assumed ────────────────
    # Every production string fits without scrolling now, which means the
    # scroller and its cue are code nothing on this run reaches. So they are
    # driven deliberately, with a body three times the longest real one: the
    # text must be clipped rather than drawn over the title, and the operator
    # must be TOLD there is more, in words, rather than by a hairline.
    print("\n  ---- the overflow path, driven on purpose ----")
    for tag, pop_, text_attr, long_text, label_id, sv_id, cue_id in (
        ("resync", probe_pop, "body_text", rmod.JOG_TEXT * 3,
         "lbl_body", "sv_body", "more_body"),
        ("offset", pop, None, REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_AT_PITCH] * 3,
         "lbl_message", "sv_message", "more_message"),
    ):
        if text_attr is None:
            pop_._show(long_text, state="refused")
        else:
            setattr(pop_, text_attr, long_text)
        settle(6)
        lbl, sv, cue = (pop_.ids[label_id], pop_.ids[sv_id], pop_.ids[cue_id])
        check("D3" if tag == "offset" else "D1", f"{tag}/overflow-is-clipped",
              sv.top <= pop_.content.top + 1 and pop_.top <= Window.height + 1,
              f"a {round(lbl.height)} px body in a {round(sv.height)} px "
              f"viewport stays inside the popup instead of climbing over the "
              f"title")
        check("D3" if tag == "offset" else "D1", f"{tag}/overflow-is-announced",
              lbl.height > sv.height + 2 and cue.opacity == 1,
              f"{cue_id} lit ({cue.text!r}) with "
              f"{round(lbl.height - sv.height)} px below the fold")
    probe_pop.body_text = rmod.JOG_TEXT
    pop._show(REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_JOB], state="refused")
    settle(6)

    # ── D4: the severity ladder, read off the RENDERED colours ──────────
    # Not off the table in the module: the defect was that the table's verdict
    # never reached a pixel. Every state is driven through the same property
    # the wizard sets, then the body colour is read back from the widget.
    print("\n  ---- D4: body colour by state, read off the widget ----")
    colours = {}
    for state in ("jog", "align", "drifted", "latched", "refused", "red_flag"):
        probe_pop.state = state
        settle(4)
        body = probe_pop.ids["lbl_body"]
        colours[state] = tuple(round(c, 3) for c in body.color)
        print(f"  COLOUR [{state:9}] severity={probe_pop.severity:8} "
              f"caption={probe_pop.severity_caption!r} colour={colours[state]}")
    theme = app.theme
    neutral = tuple(round(c, 3) for c in theme.text)
    check("D4", "red_flag/not-neutral", colours["red_flag"] != neutral,
          f"red_flag body {colours['red_flag']} vs the routine colour {neutral}")
    check("D4", "red_flag/not-latched", colours["red_flag"] != colours["latched"],
          f"red_flag {colours['red_flag']} vs latched {colours['latched']}")
    check("D4", "red_flag/is-danger",
          colours["red_flag"] == tuple(round(c, 3) for c in theme.danger_text),
          "a custody fault renders in the same alarm colour the phase-offset "
          "modal gives a refusal")
    check("D4", "latched/is-success",
          colours["latched"] == tuple(round(c, 3) for c in theme.success_text),
          f"latched body {colours['latched']}")
    check("D4", "red_flag/outranks-refused",
          probe_pop.severity != "refused",
          "red_flag and refused share danger_text, so the FILLED banner is what "
          "separates a machine fault from a button that did not take "
          f"(severity {probe_pop.severity!r} at red_flag)")
    check("D4", "captions/terminal-states-named",
          all(rmod.STATE_SEVERITY[s][1]
              for s in ("drifted", "latched", "refused", "red_flag")),
          "every state past the routine walkthrough carries a caption")

    # ── D6: the live readout is the loudest thing in the align state ────
    probe_pop.state = "align"
    probe_pop.live_text = "Z hold: +0 counts (tolerance ±3)\nSpindle: still"
    settle(6)
    live = probe_pop.ids["lbl_live"]
    body = probe_pop.ids["lbl_body"]
    dim = tuple(round(c, 3) for c in theme.text_dim)
    live_colour = tuple(round(c, 3) for c in live.color)
    check("D6", "live/bigger-than-the-prose", live.font_size > body.font_size,
          f"live readout {round(live.font_size, 1)} px vs body prose "
          f"{round(body.font_size, 1)} px")
    check("D6", "live/not-dimmed", live_colour != dim,
          f"live readout {live_colour} vs text_dim {dim}")
    check("D6", "live/is-accent",
          live_colour == tuple(round(c, 3) for c in theme.accent_text),
          "the readout is in the readout colour, like lbl_total in the offset "
          "modal")
    check("D6", "live/separated",
          round(probe_pop.ids["live_row"].height) > round(live.texture_size[1]),
          f"row {round(probe_pop.ids['live_row'].height)} px around a "
          f"{round(live.texture_size[1])} px texture -- it has a panel of its "
          f"own, not just the next line under the body")
    probe_pop.live_text = ""
    probe_pop.dismiss()
    settle(10)

    # ── D5: the not-connected refusal, produced by the production FSM ───
    offline = StubLatchHal()
    offline.connected = False
    p_off = rmod.ThreadResyncPopup()
    p_off._resync = ThreadResync(offline, app.els,
                                 read_z_counts=lambda: Z_BASELINE,
                                 read_spindle_counts=lambda: SPINDLE_BASELINE)
    p_off.begin()
    siblings = [
        "No threading job is armed", "This job already has a thread reference",
    ]
    print(f"\n  D5 not-connected refusal: {p_off.body_text!r}")
    check("D5", "resync/offline-is-a-sentence",
          len(p_off.body_text.split()) > 8 and p_off.body_text.rstrip().endswith("."),
          f"{len(p_off.body_text.split())} words, ends in a full stop")
    check("D5", "resync/offline-says-what-to-do",
          "reconnect" in p_off.body_text.lower(),
          "names the next step, the way its siblings do "
          f"({siblings[0]!r}...)")
    check("D5", "resync/offline-length-matches-siblings",
          len(p_off.body_text) >= 100,
          f"{len(p_off.body_text)} chars against siblings of 110 and 122")

    # The two refusals not in the render list, through the same entry point.
    ctl["enable"] = True
    app.board.connected = False
    pop.apply()
    record("offset / refused OFFLINE [not rendered]", state=pop.state,
           message=pop.message)
    app.board.connected = True

    saved = app.els_uic.is_threading
    app.els_uic.is_threading = False
    pop.apply()
    record("offset / refused NO_PITCH [not rendered]", state=pop.state,
           message=pop.message)
    app.els_uic.is_threading = saved

    print("\n  REFUSAL_TEXT codes covered by a rendered shot: "
          "AT_PITCH, NEGATIVE, NO_JOB")
    print(f"  ElsFsm outcome codes: "
          f"{[v for k, v in vars(ElsFsm).items() if k.startswith('PHASE_OFFSET_')]}")

    pop.dismiss()
    settle(10)


def _shot_frozen(pop, name):
    """A shot of the waiting state, with the modal's ack clock pinned.

    settle()/shot() would otherwise burn the twenty polls the ack is allowed
    and the frame would catch the timeout instead of the wait.
    """
    target = os.path.join(OUT_DIR, f"{name}.png")
    for path in (SCRATCH, target):
        if os.path.exists(path):
            os.remove(path)
    for _ in range(10):
        pop._ack_polls = 0
        EventLoop.idle()
    got = Window.screenshot(name=SCRATCH)
    if got and os.path.exists(got):
        os.remove(got)
    for _ in range(4):
        pop._ack_polls = 0
        EventLoop.idle()
    got = Window.screenshot(name=SCRATCH)
    os.replace(got, target)
    assert pop.state == "waiting", f"state slipped to {pop.state!r} mid-capture"
    print(f"WROTE {target}  ({os.path.getsize(target)} bytes)  "
          f"# state held at {pop.state!r}")


# ── run ──────────────────────────────────────────────────────────────
def _capture(_dt):
    try:
        section_strip()
        section_settings()
        section_resync()
        section_phase_offset()
    except Exception:
        import traceback
        traceback.print_exc()
    if os.path.exists(SCRATCH):
        os.remove(SCRATCH)
    print("\n########## fit measurements ##########")
    for tag, detail in MEASUREMENTS:
        print(f"  [{tag}] {detail}")

    # ── The part that makes this a check and not a printout ──────────────
    # "The code changed" is not evidence a layout defect is fixed; the pixels
    # are. Every one of the six defects fixed on 2026-08-23 has at least one
    # assertion above, made against the real widget tree at the machine's real
    # 1024x600, and this is where a regression announces itself.
    print("\n########## defect checks ##########")
    by_defect = {}
    for defect, tag, ok, _detail in CHECKS:
        passed, failed = by_defect.setdefault(defect, ([], []))
        (passed if ok else failed).append(tag)
    for defect in sorted(by_defect):
        passed, failed = by_defect[defect]
        status = "PASS" if not failed else f"FAIL ({len(failed)})"
        print(f"  {defect}: {status}  [{len(passed)}/{len(passed) + len(failed)} checks]")
        for tag in failed:
            print(f"      FAILED: {tag}")
    total_failed = sum(len(f) for _p, f in by_defect.values())
    print(f"\n  {len(CHECKS) - total_failed}/{len(CHECKS)} checks passed"
          + ("" if not total_failed else f"  -- {total_failed} FAILED"))
    print("\n########## every string rendered, in order ##########")
    for title, fields in TRANSCRIPT:
        print(f"\n[{title}]")
        for key, value in fields.items():
            print(f"    {key} = {value!r}")
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

    # REAL MACHINE GEOMETRY, for preview_phase_offset.py's reason: a fresh
    # config's placeholder ratio renders a one-step offset as "+1.111 mm", a
    # true picture of a machine nobody owns. 5 mm leadscrew at 2000 steps/rev
    # is 400 leadscrew steps per mm; elsMode first, or
    # ServoDispatcher.configure_lead_screw_ratio returns before deriving
    # ratioNum/Den and the three assignments change nothing.
    app.servo.elsMode = True
    app.servo.leadScrewPitchIn = False
    app.servo.leadScrewPitch = 5
    app.servo.leadScrewPitchSteps = 2000
    # 1.50 mm pitch from the real THREAD_MM table: 600 leadscrew steps to the
    # pitch, so a 3-start's 1/3 is exactly 200 steps / 0.500 mm.
    els_bar.set_feed_ratio("Thread MM", 8)

    def _stoponly(_d):
        # AFTER the mode swap mounts the bar. Stop-only is the mode the machine
        # is actually run in.
        adv = adv_bar()
        if adv is not None:
            adv.enable_wizard = False
            adv.enable_retract = False
        Clock.schedule_once(_capture, 1.0)

    Clock.schedule_once(_stoponly, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()
