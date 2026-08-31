"""Capture the README's screenshots: the home screen in each operator mode,
and a frame per step of the wizard.

STOP-ONLY IS THE HEADLINE, and getting that wrong is why this file was
rewritten on 2026-08-30. `ElsAdvancedBar.enable_wizard` and `enable_retract`
both default True, this script never set them, and so every screenshot the
project has ever shipped showed WIZARD mode -- complete with a Start button,
Start Z / Major o / Minor o fields and an "Engage to begin" instruction that
the machine's own operator never sees. Every preview harness under previews/
deliberately switches to stop-only and says why; the one script whose output
is the project's public face did not.

Renders at the fixed target-hardware resolution (1024x600) and composites each
PNG over THAT THEME'S background colour (``export_to_png`` produces a
transparent background because it exports the root widget, not the Window --
and the app's backdrop lives on ``Window.canvas.before``, so it is never in the
export).

Run (WSL):
    cd ui && OUT_DIR=../docs/screenshots SDL_AUDIODRIVER=dummy KIVY_NO_ARGS=1 \
        xvfb-run -a -s "-screen 0 1024x600x24" \
        ./.venv/bin/python scripts/capture_readme_screenshots.py

NOTHING REGENERATES THESE AUTOMATICALLY. The README claimed CI did until
2026-08-30; no workflow has ever referenced this script. Run it by hand after
a UI change that alters the home screen, and commit what it writes.

Output files (in OUT_DIR, default current directory):
    home_els_dark.png / home_els_light.png    stop-only -- the headline pair
    home_els_stopretract.png                  stop + retract
    wizard_1_stop_z.png ... wizard_6_ready.png  one frame per wizard step
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
from reflex.fsms.ui_controller import UI_POLICY  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR", ".")
THEMES = ("light", "dark")
MODE_ELS = 2
MODE_DRO = 4
AXIS_NAMES = ("Z", "X", "S")  # representative lathe axes (S = spindle) for the showcase
IDLE_TICKS = 6  # frames to flush before each export (texture/colors must settle)
TARGET_SIZE = [1024, 600]

FAILED = []
WROTE = []

app = MainApp()


def _composite_over_theme_bg(path, rgba):
    """Flatten a transparent PNG onto the THEME's opaque background, in place.

    NOT black. export_to_png() exports the root WIDGET, so it never captures
    Window.clearcolor or the gradient drawn on Window.canvas.before (app.py) --
    the app's actual backdrop. Regions that deliberately let that backdrop show
    (the DRO area, the gaps between advbar controls) therefore export fully
    TRANSPARENT, and whatever we flatten them onto becomes their apparent color.

    Flattening onto black was harmless in dark (background 0.055) and produced a
    black hole in light (background 0.851), which reads as a UI defect and is
    not one.

    AND THE ALPHA IS PREMULTIPLIED. Image.alpha_composite assumes STRAIGHT
    alpha, so using it here multiplied every translucent pixel by its own
    opacity a second time -- every disabled control, every dimmed panel, in
    every screenshot this script has ever produced, rendered darker than the
    machine shows. Proven rather than guessed: exporting the home screen
    without compositing and reading the disabled "Cut" card gives RGB (4, 5, 6)
    at alpha 102, against a theme recess of (10, 12, 14) and a widget opacity
    of 0.4 -- and 10 x 0.4 = 4.

    It cost a wrong finding before it was caught. "Disabled controls are
    unreadable on the light theme", measured at 1.03:1 and written into the
    Gate 2 list on 2026-08-30, was this, not a UI defect.

    For a premultiplied source the source's own alpha term is already applied,
    so the composite is just:

        out = src_rgb + (1 - src_a) * bg_rgb
    """
    img = Image.open(path).convert("RGBA")
    src = img.load()
    w, h = img.size
    br, bg_, bb = (int(round(max(0.0, min(1.0, c)) * 255)) for c in rgba[:3])
    out = Image.new("RGB", (w, h))
    dst = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            inv = (255 - a) / 255.0
            dst[x, y] = (min(255, int(round(r + inv * br))),
                         min(255, int(round(g + inv * bg_))),
                         min(255, int(round(b + inv * bb))))
    out.save(path)



# The setup screens, by the name they are registered under in the manager.
# Rendered so the commissioning pages can show what they describe; the guide
# does not walk every field, which is what the machine's own `?` help is for.
SETUP_SCREENS = [
    ("setup_screen", "setup_hub"),
    ("els_setup", "setup_els"),
    ("axes_setup", "setup_axes"),
    ("inputs_setup", "setup_inputs"),
    ("servo", "setup_servo"),
    ("formats", "setup_formats"),
]

# Regions named on the guide's "The screen" page, in reading order. Each entry
# resolves to a WIDGET, so the boxes are measured rather than typed: a layout
# change moves the annotation with it.
#
# The two bars are separate widgets and the guide had conflated them. ElsBar is
# the always-present row (Sync Enable, DIR, ADV, pitch); ElsAdvancedBar is what
# ADV reveals, and it carries the status gutter as well as the Engage/value/
# action row -- so collapsing ADV hides the reference and phase chips too.
REGIONS = [
    (1, "Sidebar", "toolbar"),
    (2, "Status bar", "statusbar"),
    (3, "DRO", "dro"),
    (4, "Status gutter", "gutter"),
    (5, "Advanced ELS bar", "advbar"),
    (6, "ELS bar", "elsbar"),
]


def _region_rects():
    """Locate each region by widget. Returns {key: (x, y, w, h)} in Kivy coords.

    Raises rather than skipping a region it cannot find: an annotation missing
    a box is worse than no annotation, because the numbers in the prose would
    then point at nothing.
    """
    home = app.manager.get_screen("home")
    layout = home.current_layout
    adv = _find(lambda w: type(w).__name__ == "ElsAdvancedBar")
    els = _find(lambda w: type(w).__name__ == "ElsBar")
    toolbar = _find(lambda w: type(w).__name__ == "HomeToolbar")
    statusbar = _find(lambda w: type(w).__name__ == "StatusBar")
    for name, w in (("ElsAdvancedBar", adv), ("ElsBar", els),
                    ("HomeToolbar", toolbar), ("StatusBar", statusbar)):
        assert w is not None, f"cannot annotate: no {name} in the tree"

    gutter = adv.ids.status_gutter.__self__

    # The DRO is not one widget: it is the axis rows plus the spindle row.
    # Take their bounding box rather than naming one of them.
    rows = list(layout.axis_bars) + [layout.spindle_info]
    x0 = min(r.x for r in rows)
    x1 = max(r.right for r in rows)
    y0 = min(r.y for r in rows)
    y1 = max(r.top for r in rows)

    # The advanced bar minus its gutter: the gutter gets its own number, and a
    # box drawn around both would put two numbers on one rectangle.
    adv_y = adv.y
    adv_h = gutter.y - adv.y

    return {
        "toolbar": (toolbar.x, toolbar.y, toolbar.width, toolbar.height),
        "statusbar": (statusbar.x, statusbar.y, statusbar.width, statusbar.height),
        "dro": (x0, y0, x1 - x0, y1 - y0),
        "gutter": (gutter.x, gutter.y, gutter.width, gutter.height),
        "advbar": (adv.x, adv_y, adv.width, adv_h),
        "elsbar": (els.x, els.y, els.width, els.height),
    }


def _annotate(src, dst):
    """Draw numbered boxes on an exported frame."""
    from PIL import ImageDraw, ImageFont

    rects = _region_rects()
    img = Image.open(src).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")

    font_path = os.path.join(os.path.dirname(reflex.__file__),
                             "fonts", "ChakraPetch-Bold.ttf")
    if not os.path.exists(font_path):
        font_path = os.path.join(os.path.dirname(reflex.__file__),
                                 "fonts", "ChakraPetch-SemiBold.ttf")
    badge_font = ImageFont.truetype(font_path, 17)

    # Amber: the one hue the UI itself does not use for state, so an annotation
    # can never be mistaken for something the screen is saying.
    INK = (255, 176, 32)
    SHADE = (0, 0, 0, 150)

    for num, label, key in REGIONS:
        x, y, w, h = rects[key]
        # Kivy y is bottom-up; PIL is top-down.
        left, top = round(x), round(H - (y + h))
        right, bottom = round(x + w), round(H - y)
        draw.rectangle([left + 1, top + 1, right - 2, bottom - 2],
                       outline=INK, width=2)

        # NUMBER ONLY. `label` stays in REGIONS as the caption the guide uses
        # and as documentation of what each box is, but drawing it here covered
        # the UI the box exists to point at.
        r = 12
        cx = left + r + 4
        cy = top + r + 4
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SHADE, outline=INK,
                     width=2)
        t = str(num)
        tb = draw.textbbox((0, 0), t, font=badge_font)
        draw.text((cx - (tb[2] - tb[0]) / 2 - tb[0],
                   cy - (tb[3] - tb[1]) / 2 - tb[1]), t, font=badge_font,
                  fill=INK)

    img.save(dst)
    print("WROTE", dst, "  %d regions" % len(REGIONS))
    return rects


def _settle():
    for _ in range(IDLE_TICKS):
        EventLoop.idle()


def _bar():
    return app.manager.get_screen("home").els_bar.ids.get("advanced_bar") \
        or _find(lambda w: type(w).__name__ == "ElsAdvancedBar")


def _find(pred, root=None):
    root = root or app.root
    if pred(root):
        return root
    for child in root.children:
        got = _find(pred, child)
        if got is not None:
            return got
    return None


def _shot(name):
    out = os.path.join(OUT_DIR, f"{name}.png")
    _settle()
    # First export under-renders (only one tile); export twice.
    app.root.export_to_png(out)
    app.root.export_to_png(out)
    assert list(app.root.size) == TARGET_SIZE, (
        f"{name}: rendered at {list(app.root.size)}, not the machine's "
        f"{TARGET_SIZE}")
    _composite_over_theme_bg(out, app.theme.background)
    WROTE.append(out)
    print("WROTE", out)


def _set_mode(bar, wizard, retract):
    """Set an operator mode and DEMAND that it took.

    The flags propagate to ElsUiController through ElsAdvancedBar's setters, so
    a silently-ignored write here would mislabel every frame below -- which is
    exactly what happened to previews/preview_takeup_banner.py, whose
    stop-only pair was captured in wizard mode for as long as it existed.
    """
    bar.enable_wizard = wizard
    bar.enable_retract = retract
    _settle()
    uic = app.els_uic
    assert bar.enable_wizard is wizard and bar.enable_retract is retract, (
        f"bar did not take wizard={wizard} retract={retract}")
    assert uic.wizard_enabled is wizard and uic.retract_enabled is retract, (
        f"the controller disagrees with the bar: wizard="
        f"{uic.wizard_enabled} retract={uic.retract_enabled}")
    print(f"  mode: wizard={wizard} retract={retract} "
          f"(bar h={round(bar.height)})")


# One frame per wizard step. The captions are not written here: the third
# element is the UI_STATE_POLICY state whose OWN instruction_text is asserted
# against the screen, so the README cannot describe a step the app does not.
WIZARD_STEPS = [
    ("wizard_1_stop_z",     "set_stop_z",     "stop_z_valid"),
    ("wizard_2_retract_z",  "set_retract_z",  "retract_z_valid"),
    ("wizard_3_start_dia",  "set_start_dia",  "start_dia_valid"),
    ("wizard_4_stop_dia",   "set_stop_dia",   "stop_dia_valid"),
    ("wizard_5_confirm",    "confirm",        None),
    ("wizard_6_ready",      "in_cycle.waiting_to_cut", None),
]


def _capture_wizard(bar):
    """Walk the wizard and photograph each step.

    Driven through the real UI FSM triggers, with each step's validity flag set
    the way committing a value would set it -- so the instruction text and the
    action button in every frame are produced by production code rather than
    posed.
    """
    uic = app.els_uic
    _set_mode(bar, wizard=True, retract=True)

    # ENGAGED, or every frame says "Engage to begin". _apply_policy overrides
    # the state's own instruction text while the domain FSM is disabled
    # (ui_controller.py:1004), so without this the six frames below carry six
    # copies of one sentence -- the wizard's actual prompts, the whole point of
    # the sequence, never appear. Caught by reading the captured text, not the
    # captured state: the ACTION BUTTON followed the FSM correctly the whole
    # time, so the frames looked like they were working.
    uic.engaged = True

    fsm = uic._ui_fsm
    fsm.start()
    _settle()

    for name, want_state, gate in WIZARD_STEPS:
        assert fsm.state == want_state, (
            f"{name}: wizard is in {fsm.state!r}, not {want_state!r} -- the "
            f"frame would carry another step's instruction under this "
            f"filename")
        # THE STATE IS NOT THE SCREEN. _apply_policy can replace a state's
        # instruction with a global one (not engaged, alarm), and it did:
        # every frame in the first run of this sequence read "Engage to
        # begin" while the FSM walked the six states correctly underneath.
        want_text = UI_POLICY[want_state]["instruction_text"]
        assert uic.instruction_text == want_text, (
            f"{name}: screen reads {uic.instruction_text!r}, but "
            f"{want_state!r} prompts {want_text!r}. Something is overriding "
            f"the step's own instruction and this frame does not show the "
            f"wizard.")
        print(f"  [{want_state}] button={uic.action_button_text!r} "
              f"instruction={uic.instruction_text!r}")
        _shot(name)
        if gate is not None:
            # What committing the value does. Set here rather than driven
            # through the axis so the walk does not depend on fabricated
            # scale positions.
            setattr(uic, gate, True)
        if want_state != WIZARD_STEPS[-1][1]:
            fsm.action()
            _settle()


def _capture(_dt):
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        # The wand must be gone on a lathe even with the setting ON.
        wand = _find(lambda w: type(w).__name__ == "IconButton"
                     and getattr(w, "icon", "") == "\ue2ca")
        assert wand is not None, (
            "cannot find the pattern-screen button to check it is hidden -- "
            "if the icon changed, this check has stopped checking anything")
        assert app.formats.show_wizard is True, "the setting must be ON here"
        assert round(wand.height) == 0 and wand.disabled, (
            f"the pattern wand is showing on a LATHE: height="
            f"{round(wand.height)} disabled={wand.disabled}. USE_CASE_PATTERNS "
            f"says a lathe has no pattern screen, so the button must be gone "
            f"regardless of the Show Patterns setting.")
        print("  pattern wand hidden by use case, with the setting ON")

        bar = _bar()
        assert bar is not None, (
            "ElsAdvancedBar is not mounted; every mode below would be "
            "whatever a fresh config booted into. Raise the delay.")

        # ── The headline pair: STOP-ONLY, the mode the machine is run in.
        _set_mode(bar, wizard=False, retract=False)
        for theme in THEMES:
            app.formats.theme = theme
            _settle()
            _shot(f"home_els_{theme}")

        # ── The annotated region map for the guide's "The screen" page.
        # Dark, stop-only: the same frame the page already shows, with its
        # regions numbered from the widgets' own geometry.
        app.formats.theme = "dark"
        _settle()
        annotated = os.path.join(OUT_DIR, "the_screen_regions.png")
        _region_rects()          # fail here, before a frame is exported
        _shot("home_els_dark")   # re-export the base at this theme
        _annotate(os.path.join(OUT_DIR, "home_els_dark.png"), annotated)
        WROTE.append(annotated)

        # ── Stop + retract, and the wizard walk. Dark only: these illustrate
        # controls and workflow, and a second theme of each would double the
        # README's images without adding anything.
        app.formats.theme = "dark"
        _settle()
        _set_mode(bar, wizard=False, retract=True)
        _shot("home_els_stopretract")

        _capture_wizard(bar)

        # ── FEED mode. Every stop mode supports plain turning, and the guide
        # says so repeatedly without ever showing it. Set through the real
        # setter so the pitch display, the ratio and is_threading all follow.
        _set_mode(bar, wizard=False, retract=False)
        home = app.manager.get_screen("home")
        # 0.005 in/rev -- an ordinary finishing feed. Imperial table,
        # because an inch DRO over a metric feed rate is nobody's machine.
        home.els_bar.set_feed_ratio("Feed IN", 4)
        _settle()
        assert not app.els_uic.is_threading, (
            "feed frame is still in a THREADING table -- is_threading gates "
            "the phase features, so this frame would misrepresent the mode")
        _shot("home_els_feed")

        # Back to a threading table: later frames and any future step should
        # not inherit feed mode from this one.
        # Back to a threading table: 16 TPI, the thread the belt-off
        # verification run cut on the real machine.
        home.els_bar.set_feed_ratio("Thread IN", 9)
        _settle()
        assert app.els_uic.is_threading, "failed to return to a threading table"

        # ── DRO mode: the plain read-out, no leadscrew row at all.
        app.set_mode(MODE_DRO)
        _settle()
        for _ in range(30):          # the mode swap is Clock-deferred
            EventLoop.idle()
        assert app.current_mode == MODE_DRO, (
            f"still in mode {app.current_mode}, not DRO")
        _shot("home_dro")
        app.set_mode(MODE_ELS)
        for _ in range(30):
            EventLoop.idle()
        assert app.current_mode == MODE_ELS

        # ── The setup screens.
        for name, out in SETUP_SCREENS:
            app.manager.goto(name)
            for _ in range(30):
                EventLoop.idle()
            assert app.manager.current == name, (
                f"asked for screen {name!r} and landed on "
                f"{app.manager.current!r} -- {out}.png would be a picture of "
                f"the wrong screen")
            _shot(out)
        app.manager.goto("home")
        for _ in range(30):
            EventLoop.idle()
    except Exception as e:  # noqa: BLE001 - capture script, want the full traceback
        import traceback
        traceback.print_exc()
        print("CAPTURE FAILED", e)
        FAILED.append(e)
    app.stop()


def _arm(_dt):
    # Lathe use case is what exposes ELS mode (set_mode silently falls back to
    # DRO otherwise — see app.allowed_modes / USE_CASE_MODES).
    app.use_case = "lathe"

    # THIS CAPTURE REPRESENTS A PROVISIONED LATHE, NOT A FRESH INSTALL. The
    # isolated HOME above is for determinism, and its cost is that every
    # setting not overridden here comes out at its class default -- which is
    # how these shots shipped in wizard mode until 2026-08-30.
    #
    # THE PATTERN WAND IS LEFT SWITCHED ON, DELIBERATELY. It sits in the
    # sidebar above ELS/DRO and opens the pattern screen -- a rotary-table
    # feature. This capture used to force `show_wizard = False` to keep it out
    # of the shots; since 2026-08-30 a lathe does not expose the pattern screen
    # at all (USE_CASE_PATTERNS in app.py), so leaving the setting at its
    # default True and asserting the button is gone PROVES the use-case gate
    # instead of hiding behind the setting.
    app.formats.show_wizard = True

    # INCHES, matching the machine (elspi: current_format IN). The default is
    # MM, and a default is not a decision -- see the note above.
    app.formats.current_format = "IN"
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
    # A pitch every frame can show: 16 TPI. Set before the first capture so the
    # headline shots do not carry a fresh config's metric default.
    app.manager.get_screen("home").els_bar.set_feed_ratio("Thread IN", 9)
    app.manager.get_screen("home").els_bar.enable_advanced = True
    # Mode swap is deferred via Clock (see HomePage.change_mode); give it time
    # for the ELS layout to mount before capturing. The modes themselves are
    # set INSIDE _capture, after the bar exists -- setting them here would be
    # silently dropped, which is the bug that made preview_takeup_banner.py
    # photograph the wrong mode for its whole life.
    Clock.schedule_once(_capture, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()

# A CAPTURE SCRIPT THAT WRITES FILES AND EXITS 0 IS NOT EVIDENCE IT WORKED.
# The except above exists so the traceback survives Kivy's clock; it must not
# also swallow the exit code.
if FAILED:
    raise SystemExit(f"capture_readme_screenshots failed: {FAILED[0]!r}")
EXPECTED = 2 + 1 + 1 + 1 + len(WIZARD_STEPS) + 2 + len(SETUP_SCREENS)
if len(WROTE) != EXPECTED:
    raise SystemExit(
        f"wrote {len(WROTE)} images, expected {EXPECTED}: "
        f"{[os.path.basename(w) for w in WROTE]}")
print(f"OK: {len(WROTE)} images")
