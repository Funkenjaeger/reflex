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
    cd ui && OUT_DIR=docs/screenshots SDL_AUDIODRIVER=dummy KIVY_NO_ARGS=1 \
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

        # ── Stop + retract, and the wizard walk. Dark only: these illustrate
        # controls and workflow, and a second theme of each would double the
        # README's images without adding anything.
        app.formats.theme = "dark"
        _settle()
        _set_mode(bar, wizard=False, retract=True)
        _shot("home_els_stopretract")

        _capture_wizard(bar)
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
if len(WROTE) != 2 + 1 + len(WIZARD_STEPS):
    raise SystemExit(
        f"wrote {len(WROTE)} images, expected {2 + 1 + len(WIZARD_STEPS)}: "
        f"{[os.path.basename(w) for w in WROTE]}")
print(f"OK: {len(WROTE)} images")
