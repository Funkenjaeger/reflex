"""Drive the app through a scripted ELS sequence, recording UI state codes.

Produces the input a storyboard is built from, without needing a lathe. Used by
the ``render``-marked replay test, and by hand when demonstrating the feature:

    OUTDIR=/tmp/uistate xvfb-run -a -s "-screen 0 1024x600x24" \
        SDL_AUDIODRIVER=dummy uv run python previews/preview_uistate_session.py
    xvfb-run -a -s "-screen 0 1024x600x24" SDL_AUDIODRIVER=dummy \
        uv run python scripts/replay_ui_state.py /tmp/uistate/uistate.jsonl \
            --out /tmp/story --strict
    uv run python scripts/storyboard.py /tmp/story

THE CONTROLLER PROPERTIES ARE SET DIRECTLY, not driven through the FSM. This is
a rendering fixture, not a behavioural one: the point is to put a variety of
pictures on screen (banners, instructions, a mode swap, a theme change) so the
capture/replay path is exercised across them. Do not read it as a description of
states the machine can actually reach.
"""
import os
import tempfile

# Before any Kivy or reflex import -- dispatchers resolve their YAML from HOME,
# and this must not read or write a developer's real config.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-uistate-session-")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("REFLEX_UISTATE_DIR", os.environ.get("OUTDIR", "/tmp/uistate"))

from kivy.config import Config  # noqa: E402

Config.set("graphics", "width", "1024")
Config.set("graphics", "height", "600")

import reflex  # noqa: E402
from kivy.resources import resource_add_path  # noqa: E402

resource_add_path(os.path.dirname(reflex.__file__))

from kivy.base import EventLoop  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.logger import Logger  # noqa: E402

from reflex.app import MainApp  # noqa: E402

log = Logger.getChild(__name__)

MODE_ELS = 2
MODE_DRO = 4
AXIS_NAMES = ("Z", "X", "S")
STEP_SECONDS = 0.7

app = MainApp()


def commission():
    """A lathe with named Z/X/spindle axes -- what ELS mode needs to render."""
    app.use_case = "lathe"
    for index, name in enumerate(AXIS_NAMES):
        if index < len(app.axes):
            app.axes[index].axis_name = name
    app.els.z_axis_index = 0
    app.els.x_axis_index = 1
    app.els.spindle_axis_index = 2
    app.manager.goto("home")
    app.set_mode(MODE_ELS)


def show_advanced():
    app.manager.get_screen("home").els_bar.enable_advanced = True


def wizard_prompt():
    app.els_uic.instruction_text = "Go to stop Z position and press Set"
    app.els_uic.action_button_text = "Set"
    app.els_uic.active_input = "stop_z"


def cutting():
    app.els_uic.ui_state = "in_cycle.cutting"
    app.els_uic.instruction_text = "Cutting..."
    app.els_uic.action_button_text = ""
    app.els_uic.active_input = ""
    app.els_uic.in_cycle = True


def takeup_banner():
    app.els_uic.takeup_warning = "Take-up exceeded margin"


def light_theme():
    app.formats.theme = "light"


def dro_mode():
    app.els_uic.takeup_warning = ""
    app.set_mode(MODE_DRO)


STEPS = (commission, show_advanced, wizard_prompt, cutting, takeup_banner,
         light_theme, dro_mode)


def run_step(index, _dt=None):
    if index >= len(STEPS):
        Clock.schedule_once(finish, 1.0)
        return
    try:
        STEPS[index]()
    except Exception as e:  # noqa: BLE001 - a fixture; show the whole failure
        log.exception(f"uistate session step {STEPS[index].__name__} failed: {e}")
    for _ in range(3):
        EventLoop.idle()
    Clock.schedule_once(lambda _d: run_step(index + 1), STEP_SECONDS)


def finish(_dt):
    print(f"RECORDS {app.uistate.records_written} -> {app.uistate.path}")
    app.stop()


Clock.schedule_once(lambda _d: run_step(0), 2.5)
app.run()
