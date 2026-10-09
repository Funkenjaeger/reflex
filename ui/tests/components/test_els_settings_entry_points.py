"""The ELS settings popup opens from two places: the advanced ELS bar's
settings button and Setup -> ELS -> ELS Setup. Both must open the SAME popup on
the SAME live state -- the home screen's one advanced bar, the app's one ELS
dispatcher and servo -- so a value shown or changed from one is the value seen
from the other. A second ElsAdvancedBar, or a copy of its settings, would be a
second source of truth.

Built headlessly like test_els_advbar.py: the popup's and the screen's kv rules
are patched out (the mock GL backend cannot build their graphics), and
Popup.open is recorded instead of run. Every popup method and property under
test is the real one.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def opened(monkeypatch):
    """Patch the popup so it can be built headlessly; record each one opened."""
    from reflex.components.home.els_settings_popup import ElsSettingsPopup
    seen = []
    monkeypatch.setattr(ElsSettingsPopup, "apply_class_lang_rules",
                        lambda self, *a, **kw: None)
    monkeypatch.setattr(ElsSettingsPopup, "open",
                        lambda self, *a, **kw: seen.append(self))
    return seen


@pytest.fixture
def live_app(running_app):
    """The state both entry points must share: a stateful ELS dispatcher
    stand-in (the popup writes the take-up and limits back to it)."""
    running_app.els = SimpleNamespace(
        els_backlash_steps=403, els_cal_ceiling_steps=1008,
        els_cal_motion_thresh_counts=2, els_overshoot_margin_counts=1,
        els_overshoot_correction=False, els_ratio_check=False)
    return running_app


@pytest.fixture
def home_and_setup(advbar_factory, live_app):
    """The home screen's advanced bar, and an ELS Setup screen whose app can
    reach it through the screen manager the way the running app does."""
    from reflex.components.screens.els_setup_screen import ElsSetupScreen
    from reflex.components.screens.home_screen import HomePage

    bar = advbar_factory(els_bar=None)
    home = SimpleNamespace(mode_layouts={2: SimpleNamespace(els_adv_bar=bar)})
    home.els_advanced_bar = lambda: HomePage.els_advanced_bar(home)
    live_app.manager = SimpleNamespace(
        get_screen=lambda name: {"home": home}[name])
    with patch.object(ElsSetupScreen, "apply_class_lang_rules"):
        screen = ElsSetupScreen(name="els_setup")
    return bar, screen


def _values(popup):
    return (popup.backlash_mm, popup.cal_ceiling_mm,
            popup.cal_motion_thresh_counts, popup.overshoot_margin_counts,
            popup.get_thread_types(), popup.has_gearbox, popup.gear_names)


def test_both_entry_points_open_the_popup_on_the_one_advanced_bar(
        home_and_setup, opened):
    bar, screen = home_and_setup
    bar.open_settings()
    screen.open_els_settings()
    assert len(opened) == 2
    from_bar, from_setup = opened
    assert from_bar.bar is bar
    assert from_setup.bar is bar
    assert _values(from_setup) == _values(from_bar)


def test_a_change_from_setup_is_seen_from_the_advanced_bar(home_and_setup, opened):
    bar, screen = home_and_setup
    screen.open_els_settings()
    from_setup = opened[-1]
    from_setup.backlash_mm = 1.0                    # the NumberItem's on_value
    from_setup.overshoot_margin_counts = 3
    from_setup.on_thread_type_selected(from_setup.get_thread_types()[-1])
    from_setup.bar.inner_thread = not from_setup.bar.inner_thread

    bar.open_settings()
    from_bar = opened[-1]
    assert from_bar.backlash_mm == pytest.approx(1.0, abs=127 / 64000)
    assert from_bar.overshoot_margin_counts == 3
    assert from_bar.bar.thread_profile_type == from_setup.get_thread_types()[-1]
    assert from_bar.bar.inner_thread == from_setup.bar.inner_thread


def test_a_change_from_the_advanced_bar_is_seen_from_setup(home_and_setup, opened):
    bar, screen = home_and_setup
    bar.open_settings()
    opened[-1].cal_ceiling_mm = 2.0
    opened[-1].cal_motion_thresh_counts = 5

    screen.open_els_settings()
    from_setup = opened[-1]
    assert from_setup.cal_ceiling_mm == pytest.approx(2.0, abs=127 / 64000)
    assert from_setup.cal_motion_thresh_counts == 5


def test_with_no_home_screen_setup_opens_nothing(live_app, opened):
    """Never a popup on a stand-in bar: without the home screen's bar there is
    no shared state to edit."""
    from reflex.components.screens.els_setup_screen import ElsSetupScreen
    live_app.manager = SimpleNamespace(get_screen=lambda name: None)
    with patch.object(ElsSetupScreen, "apply_class_lang_rules"):
        screen = ElsSetupScreen(name="els_setup")
    screen.open_els_settings()
    assert opened == []


def test_the_setup_entry_is_hidden_without_els_mode():
    """Same rule as the Setup screen's ELS button. The screen is never built
    here, so pin the kv: the row opens the popup, reads as the popup's own
    title, and collapses for a use case without ELS."""
    import reflex.components.screens.els_setup_screen as mod
    from reflex.components.home import els_settings_popup as popup_mod

    kv = (Path(mod.__file__).parent / "els_setup_screen.kv").read_text(encoding="utf-8")
    block = kv[kv.index("on_release: root.open_els_settings()") - 600:]
    block = block[block.index("ButtonItem:"):]
    block = block[:block.index("\n\n")] if "\n\n" in block else block
    gate = "MODE_ELS in USE_CASE_MODES.get(app.use_case, [])"
    assert f"opacity: 1 if {gate} else 0" in block
    assert f"height: 60 if {gate} else 0" in block
    assert "disabled: MODE_ELS not in USE_CASE_MODES.get(app.use_case, [])" in block

    popup_kv = (Path(popup_mod.__file__).parent / "els_settings_popup.kv").read_text(
        encoding="utf-8")
    title = popup_kv.split('title: "', 1)[1].split('"', 1)[0]
    assert f'name: "{title}"' in block
