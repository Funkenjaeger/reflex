"""Construction + wiring tests for ElsAdvancedBar (reflex/components/home/els_advbar.py).

These exist because the ELS widget layer had ZERO construction tests before
this file: a real on-machine failure shipped because a dialog's Python wiring
was never exercised (see tests/components/test_custom_popup.py for the
precedent/pattern this file follows). Every test here asserts on a
controller/method CALL, not just "it didn't raise" — so a wiring break (wrong
method routed to, a kwarg silently dropped, a callback that never runs) fails
structurally instead of passing by accident.

Scope: pure Python wiring. apply_class_lang_rules is patched out so the
widget's own kv rule tree (real child widgets + graphics) never gets built —
the mock GL backend segfaults on real textures (verified independently while
building this suite; see test_keypad.py for the concrete crash).
"""
from unittest.mock import MagicMock, patch

import pytest
from kivy.event import EventDispatcher
from kivy.properties import BooleanProperty, StringProperty

from reflex.feeds import table as FEEDS_TABLE


class _FakeElsBar(EventDispatcher):
    """A real (tiny) EventDispatcher standing in for ElsBar, so
    `self.els_bar.bind(mode_name=...)` in ElsAdvancedBar.__init__ is a REAL
    Kivy binding -- exercising the actual wiring, not just calling the
    handler by hand."""
    mode_name = StringProperty("Feed MM")
    feed_name = StringProperty("0.10")
    els_forward = BooleanProperty(True)


# ── 1. Construction + mode-flag mirroring ───────────────────────────────────

@pytest.mark.parametrize("enable_wizard,enable_retract", [
    (True, True),
    (False, False),
    (True, False),
    (False, True),
])
def test_construction_mirrors_wizard_and_retract_flags_into_controller(
    advbar_factory, running_app, enable_wizard, enable_retract,
):
    bar = advbar_factory(els_bar=None, enable_wizard=enable_wizard, enable_retract=enable_retract)

    assert bar.controller.wizard_enabled is enable_wizard
    assert bar.controller.retract_enabled is enable_retract


def test_construction_headless_with_no_els_bar_does_not_crash(advbar_factory, running_app):
    bar = advbar_factory(els_bar=None)
    assert bar.els_bar is None
    assert bar.controller is running_app.els_uic


# ── 2. on_value_long_press routing ──────────────────────────────────────────

LONG_PRESS_CASES = [
    ("stop_z", "commit_standalone_stop_z", "get_z_axis"),
    ("start_z", "commit_standalone_retract_z", "get_z_axis"),
    ("major_dia", "commit_standalone_start_dia", "get_x_axis"),
    ("minor_dia", "commit_standalone_stop_dia", "get_x_axis"),
]


@pytest.mark.parametrize("which,commit_attr,axis_getter", LONG_PRESS_CASES)
def test_on_value_long_press_routes_to_correct_commit(
    advbar_factory, running_app, make_axis, which, commit_attr, axis_getter,
):
    axis = make_axis(position=42.5)
    getattr(running_app.els, axis_getter).return_value = axis
    bar = advbar_factory(els_bar=None)

    bar.on_value_long_press(which)

    getattr(bar.controller, commit_attr).assert_called_once_with(42.5)
    bar.controller.try_advance_wizard.assert_called_once()
    # Only the routed commit method fired -- no cross-wiring to a sibling target.
    other_attrs = {c for _, c, _ in LONG_PRESS_CASES} - {commit_attr}
    for other in other_attrs:
        getattr(bar.controller, other).assert_not_called()


@pytest.mark.parametrize("which,axis_getter", [
    ("stop_z", "get_z_axis"),
    ("start_z", "get_z_axis"),
    ("major_dia", "get_x_axis"),
    ("minor_dia", "get_x_axis"),
])
def test_on_value_long_press_with_no_axis_shows_popup_and_does_not_commit(
    advbar_factory, running_app, which, axis_getter,
):
    getattr(running_app.els, axis_getter).return_value = None
    bar = advbar_factory(els_bar=None)

    # The unconfigured-axis guard must give the SAME feedback as the
    # keypad-entry paths (an "Axis Not Configured" dialog) — a silent return
    # left the long-press indistinguishable from a dead button.
    with patch(
        "reflex.components.popups.custom_popup.CustomPopup"
    ) as popup_cls:
        bar.on_value_long_press(which)  # must not raise

    assert popup_cls.call_count == 1
    assert "Axis not configured" in popup_cls.call_args.kwargs.get("title", "")
    popup_cls.return_value.open.assert_called_once()
    for commit_attr in {c for _, c, _ in LONG_PRESS_CASES}:
        getattr(bar.controller, commit_attr).assert_not_called()
    bar.controller.try_advance_wizard.assert_not_called()


# ── 3. Keypad callback wiring ────────────────────────────────────────────────

# (open-method name, args to pass it, commit method name, axis getter,
#  whether show_with_callback is expected to receive use_current_fn)
KEYPAD_CASES = [
    ("_open_standalone_stop_z_keypad", (), "commit_standalone_stop_z", "get_z_axis", True),
    ("_open_standalone_start_z_keypad", (), "commit_standalone_retract_z", "get_z_axis", True),
    ("_open_standalone_diameter_keypad", ("major",), "commit_standalone_start_dia", "get_x_axis", False),
    ("_open_standalone_diameter_keypad", ("minor",), "commit_standalone_stop_dia", "get_x_axis", False),
]


@pytest.mark.parametrize("open_method,args,commit_attr,axis_getter,expects_use_current", KEYPAD_CASES)
def test_keypad_callback_commits_valid_value_and_advances_wizard(
    advbar_factory, running_app, make_axis, open_method, args, commit_attr, axis_getter, expects_use_current,
):
    axis = make_axis(position=7.0)
    getattr(running_app.els, axis_getter).return_value = axis
    bar = advbar_factory(els_bar=None)

    with patch("reflex.components.popups.keypad.Keypad") as mock_keypad_cls:
        mock_keypad = mock_keypad_cls.return_value
        getattr(bar, open_method)(*args)

    mock_keypad_cls.assert_called_once()
    mock_keypad.show_with_callback.assert_called_once()
    kwargs = mock_keypad.show_with_callback.call_args.kwargs
    assert "callback_fn" in kwargs
    if expects_use_current:
        assert kwargs.get("use_current_fn") is not None
    else:
        assert kwargs.get("use_current_fn") is None

    callback_fn = kwargs["callback_fn"]
    callback_fn("1.234")

    getattr(bar.controller, commit_attr).assert_called_once_with(1.234)
    bar.controller.try_advance_wizard.assert_called_once()


@pytest.mark.parametrize("open_method,args,commit_attr,axis_getter,expects_use_current", KEYPAD_CASES)
def test_keypad_callback_with_invalid_text_does_not_commit_or_advance(
    advbar_factory, running_app, make_axis, open_method, args, commit_attr, axis_getter, expects_use_current,
):
    axis = make_axis(position=7.0)
    getattr(running_app.els, axis_getter).return_value = axis
    bar = advbar_factory(els_bar=None)

    with patch("reflex.components.popups.keypad.Keypad") as mock_keypad_cls:
        mock_keypad = mock_keypad_cls.return_value
        getattr(bar, open_method)(*args)

    callback_fn = mock_keypad.show_with_callback.call_args.kwargs["callback_fn"]
    callback_fn("abc")  # float("abc") raises ValueError -- the guard under test

    getattr(bar.controller, commit_attr).assert_not_called()
    bar.controller.try_advance_wizard.assert_not_called()


@pytest.mark.parametrize("open_method,commit_attr,axis_getter", [
    ("_open_standalone_stop_z_keypad", "commit_standalone_stop_z", "get_z_axis"),
    ("_open_standalone_start_z_keypad", "commit_standalone_retract_z", "get_z_axis"),
])
def test_keypad_use_current_commits_live_axis_position(
    advbar_factory, running_app, make_axis, open_method, commit_attr, axis_getter,
):
    axis = make_axis(position=99.9)
    getattr(running_app.els, axis_getter).return_value = axis
    bar = advbar_factory(els_bar=None)

    with patch("reflex.components.popups.keypad.Keypad") as mock_keypad_cls:
        mock_keypad = mock_keypad_cls.return_value
        getattr(bar, open_method)()

    use_current_fn = mock_keypad.show_with_callback.call_args.kwargs["use_current_fn"]
    use_current_fn()

    getattr(bar.controller, commit_attr).assert_called_once_with(99.9)
    bar.controller.try_advance_wizard.assert_called_once()


# ── 4. Axis-not-configured guard ────────────────────────────────────────────

@pytest.mark.parametrize("open_method,args,axis_getter", [
    ("_open_standalone_stop_z_keypad", (), "get_z_axis"),
    ("_open_standalone_start_z_keypad", (), "get_z_axis"),
    ("_open_standalone_diameter_keypad", ("major",), "get_x_axis"),
    ("_open_standalone_diameter_keypad", ("minor",), "get_x_axis"),
])
def test_axis_not_configured_opens_custom_popup_and_never_builds_keypad(
    advbar_factory, running_app, open_method, args, axis_getter,
):
    getattr(running_app.els, axis_getter).return_value = None
    bar = advbar_factory(els_bar=None)

    with patch("reflex.components.popups.keypad.Keypad") as mock_keypad_cls, \
         patch("reflex.components.popups.custom_popup.CustomPopup") as mock_popup_cls:
        getattr(bar, open_method)(*args)

    mock_keypad_cls.assert_not_called()
    mock_popup_cls.assert_called_once()
    mock_popup_cls.return_value.open.assert_called_once()
    # Sanity: the dialog actually explains what's wrong, not a blank/default title.
    assert "Axis not configured" in mock_popup_cls.call_args.kwargs.get("title", "")


# ── 5. _sync_is_threading ────────────────────────────────────────────────────

@pytest.mark.parametrize("mode_name", list(FEEDS_TABLE.keys()))
def test_sync_is_threading_at_construction_matches_table_kind(advbar_factory, running_app, mode_name):
    """Threading tables ("Thread ...") -> is_threading True; feed tables
    ("Feed ...") -> False. Uses the real reflex/feeds.py table names, not the
    string-matching implementation, so this survives a swap to feeds-mode-int
    detection as long as the observable behavior is preserved."""
    fake_bar = _FakeElsBar(mode_name=mode_name)
    bar = advbar_factory(els_bar=fake_bar)

    expected = mode_name.startswith("Thread")
    assert bar.controller.is_threading == expected


def test_sync_is_threading_rebinds_live_when_mode_name_changes(advbar_factory, running_app):
    """Exercises the actual `els_bar.bind(mode_name=...)` wiring (not just a
    direct call to _sync_is_threading), using a real EventDispatcher els_bar
    so a broken/removed bind call fails this test."""
    fake_bar = _FakeElsBar(mode_name="Feed MM")
    bar = advbar_factory(els_bar=fake_bar)
    assert bar.controller.is_threading is False

    fake_bar.mode_name = "Thread IN"
    assert bar.controller.is_threading is True

    fake_bar.mode_name = "Feed IN"
    assert bar.controller.is_threading is False


def test_the_feed_rebinds_live_when_the_els_bar_changes(advbar_factory, running_app):
    """The real `els_bar.bind(feed_name=..., mode_name=...)` wiring: the
    controller reads these at each stop to label the overshoot sweep."""
    fake_bar = _FakeElsBar(mode_name="Feed MM", feed_name="0.10")
    bar = advbar_factory(els_bar=fake_bar)
    assert (bar.controller.feed_name, bar.controller.feed_unit) == ("0.10", "mm/rev")
    fake_bar.mode_name, fake_bar.feed_name = "Feed IN", "0.020"
    assert (bar.controller.feed_name, bar.controller.feed_unit) == ("0.020", "in/rev")


# ── Active gear readout ─────────────────────────────────────────────────────

class _FakeServo(EventDispatcher):
    """Stands in for the servo dispatcher: a real StringProperty, so the
    bar's binding is a real Kivy binding."""
    gearbox_engaged = StringProperty("A")


def test_advbar_shows_the_active_gear_and_follows_changes(advbar_factory, running_app):
    running_app.servo = _FakeServo(gearbox_engaged="B")
    bar = advbar_factory(els_bar=None)
    assert bar.gear_text == "Gear B"

    # Same path the coupling check takes when it adopts a new gear.
    running_app.servo.gearbox_engaged = "C"
    assert bar.gear_text == "Gear C"


@pytest.mark.parametrize("name", ["Lo", "Hi", "1", "Top"])
def test_advbar_shows_owner_names_up_to_three_characters(advbar_factory, running_app, name):
    running_app.servo = _FakeServo(gearbox_engaged=name)
    bar = advbar_factory(els_bar=None)
    assert bar.gear_text == f"Gear {name}"


def test_advbar_shows_nothing_with_no_gearbox(advbar_factory, running_app):
    """No gearbox: the servo's engaged position is "", and no gear is
    implied -- not "Gear A", not "Gear "."""
    running_app.servo = _FakeServo(gearbox_engaged="")
    bar = advbar_factory(els_bar=None)
    assert bar.gear_text == ""
    # A table configured later shows up; cleared again, it goes away.
    running_app.servo.gearbox_engaged = "Lo"
    assert bar.gear_text == "Gear Lo"
    running_app.servo.gearbox_engaged = ""
    assert bar.gear_text == ""


def _label_gear_block():
    from pathlib import Path
    import reflex.components.home.els_advbar as mod

    kv = (Path(mod.__file__).parent / "els_advbar.kv").read_text(encoding="utf-8")
    block = kv[kv.index("id: label_gear"):]
    return kv, block[:block.index("\n\n")]


def test_the_kv_gear_label_is_hidden_without_a_gearbox():
    _kv, block = _label_gear_block()
    assert "opacity: 1 if root.gear_text else 0" in block


def test_the_gear_label_clears_the_reference_chip_in_every_theme():
    """The label's left edge sits at a fixed dp offset in the status gutter;
    the reference chip is pinned left and sized to its text. Measure the
    chip's widest text in every theme's mono face (the chip's kv: 11 dp mono,
    9 dp padding each side) and require a gap before the label. The label
    grows rightwards, so a three-character name cannot close it; it must
    also stay well left of the gutter's centred texts."""
    import re
    from pathlib import Path
    from PIL import ImageFont
    import reflex
    from reflex.components.widgets.palettes import PALETTES

    kv, block = _label_gear_block()
    x = float(re.search(r"x: status_gutter\.x \+ dp\((\d+(?:\.\d+)?)\)", block).group(1))
    root = Path(reflex.__file__).parent
    for theme, palette in PALETTES.items():
        mono = root / palette["font_mono"]
        chip_font = ImageFont.truetype(str(mono), 11)
        chip = max(chip_font.getlength(t) for t in ("NO REFERENCE", "REF LATCHED")) + 18
        assert chip + 8 <= x, (theme, chip, x)
        label = ImageFont.truetype(str(mono), 12).getlength("Gear WWW")
        assert x + label <= 250, (theme, x + label)


def test_the_kv_gear_label_reads_the_live_text():
    """The widget tree is never built in this suite, so pin the kv binding:
    a label bound to a constant would pass the property test above."""
    _kv, block = _label_gear_block()
    assert "text: root.gear_text" in block



# ── the overshoot sweep's strip and feed ───────────────────────────────

class _TapController:
    def __init__(self, takes=True):
        self.takes = takes
        self.taps = 0

    def measure_cue_tapped(self):
        self.taps += 1
        return self.takes


def _strip(y, takes=True):
    from types import SimpleNamespace
    from kivy.uix.widget import Widget
    from reflex.components.home.els_advbar import MeasureCueStrip
    gutter = Widget(size_hint=(None, None), pos=(107, 230), size=(917, 26))
    ctl = _TapController(takes)
    strip = MeasureCueStrip(size_hint=(None, None), pos=(107, y), size=(917, 26),
                            gutter=gutter, controller=ctl)
    touch = SimpleNamespace(pos=(566, y + 13), x=566, y=y + 13,
                            grab_current=None, ud={})
    return strip, ctl, touch


def test_a_tap_on_the_cue_strip_over_the_gutter_opens_the_measure_window():
    strip, ctl, touch = _strip(230)
    assert strip.on_touch_down(touch) is True
    assert ctl.taps == 1


def test_a_tap_with_no_sweep_running_is_passed_on():
    strip, ctl, touch = _strip(230, takes=False)
    assert not strip.on_touch_down(touch)
    assert ctl.taps == 1


def test_a_tap_on_the_cue_strip_over_the_control_row_is_passed_on():
    """With another strip up above it, the cue strip lies over the control
    row's top, where a tap belongs to the button under it."""
    strip, ctl, touch = _strip(204)
    assert not strip.on_touch_down(touch)
    assert ctl.taps == 0


def test_a_collapsed_cue_strip_takes_no_tap():
    """No cue: the kv collapses the strip to zero height and disables it."""
    strip, ctl, touch = _strip(230)
    strip.height = 0
    strip.disabled = True
    assert not strip.on_touch_down(touch)
    assert ctl.taps == 0


def test_the_kv_cue_strip_is_the_tappable_one_and_knows_the_gutter():
    from pathlib import Path
    import reflex.components.home.els_advbar as mod
    kv = (Path(mod.__file__).parent / "els_advbar.kv").read_text(encoding="utf-8")
    block = kv[kv.index("id: measure_cue") - 40:]
    block = block[:block.index("\n\n")]
    assert "MeasureCueStrip:" in block
    assert "gutter: status_gutter" in block
    assert "controller: root.controller" in block


def test_the_feed_on_the_els_bar_is_mirrored_into_the_controller():
    from types import SimpleNamespace
    from reflex.components.home.els_advbar import ElsAdvancedBar
    bar = SimpleNamespace(
        els_bar=SimpleNamespace(feed_name="0.020", mode_name="Feed IN"),
        controller=SimpleNamespace(feed_name="", feed_unit=""))
    ElsAdvancedBar._sync_feed(bar)
    assert (bar.controller.feed_name, bar.controller.feed_unit) == ("0.020", "in/rev")
    bar.els_bar.mode_name, bar.els_bar.feed_name = "Feed MM", "0.10"
    ElsAdvancedBar._sync_feed(bar)
    assert (bar.controller.feed_name, bar.controller.feed_unit) == ("0.10", "mm/rev")
