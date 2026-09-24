"""The uncommissioned state has to reach the OPERATOR, not the log file.

WHY THIS FILE IS NOT ALLOWED TO ASSERT ON A LOG LINE. There is no terminal at
this machine. A ``log.warning`` that the card is uncommissioned is a message to
whoever reads the journal next week; the person who needs it is standing at the
lathe looking at a UI showing plausible numbers for a machine nobody has
measured. So the state counts as expressed only if it is on the screen, and
this file asserts through the widget's own behaviour and through the kv that
renders it.

WHAT CAN AND CANNOT BE EXECUTED HERE, stated plainly rather than implied.
``conftest.py`` forces ``KIVY_GL_BACKEND=mock`` for the whole suite, and under
it building a kv rule tree that carries a canvas SEGFAULTS the interpreter
(measured while writing this file: ``UncommissionedBanner()`` with its rule
applied dies in ``Builder._build_canvas`` -> texture populate; constructing a
real ``ThemeProvider`` dies the same way in ``gradients.vgrad_texture``). That
is why every widget test in this package patches ``apply_class_lang_rules``
out, and why ``test_statusbar_peak.py`` reads its kv as text. So:

  * the COLLAPSE -- whether the banner takes space on a commissioned machine --
    is Python, in ``UncommissionedBanner._apply_active``, and is executed here;
  * the WIRING -- that the home screen instantiates it and that it follows
    ``app.uncommissioned`` -- is kv, and is read as text here.

That split is deliberate: the half that must not silently do the wrong thing is
on the side a test can run.

THE SHAPE OF THE DEFECT BEING GUARDED. ``cdb6b4c`` added
``StatusBar.cycles_peak``, polled it every second, and never referenced it in
the kv; every layer was individually correct and the number reached nobody. A
banner class with perfect text that no screen instantiates, or an
``app.uncommissioned`` that nothing binds, is the same defect.

SEEN-RED -- each mutation applied alone, then reverted (``git diff
--exit-code`` clean after every one). Measured 2026-09-22 against this file:

  * ``STATE_WORD = "UNCOMMISSIONED"`` -> ``"NOT READY"`` (1 failed, 13 passed):
    ``test_the_banner_names_the_state_in_the_operators_words``.
  * ``_apply_active`` height made unconditional, i.e. the strip never collapses
    (2 failed, 12 passed): ``test_a_commissioned_machine_sees_nothing``,
    ``test_it_starts_collapsed``.
  * ``self.disabled = not self.active`` -> ``= False``, i.e. an invisible strip
    that still takes touches (1 failed, 13 passed):
    ``test_a_collapsed_strip_swallows_no_touches``.
  * ``active: app.uncommissioned`` deleted from uncommissioned_banner.kv
    (1 failed, 13 passed): ``test_the_banner_is_bound_to_the_app_state``.
  * the ``UncommissionedBanner:`` child removed from home_screen.kv (2 failed,
    12 passed): ``test_the_home_screen_instantiates_the_banner``,
    ``test_the_banner_sits_in_the_bars_container``.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reflex.components.home.uncommissioned_banner import (BANNER_HEIGHT_DP,
                                                          STATE_WORD,
                                                          UncommissionedBanner)

_COMPONENTS = Path(__file__).resolve().parents[2] / "reflex" / "components"
BANNER_KV = _COMPONENTS / "home" / "uncommissioned_banner.kv"
HOME_KV = _COMPONENTS / "screens" / "home_screen.kv"
HOME_PY = _COMPONENTS / "screens" / "home_screen.py"


@pytest.fixture
def banner():
    """A real UncommissionedBanner, built without its kv rule tree.

    Same device as this package's ``advbar_factory``: the Python the widget
    actually runs is exercised, the canvas that would take the interpreter down
    under the mock GL backend is not built. See the module docstring.
    """
    def _make(**kwargs):
        with patch.object(UncommissionedBanner, "apply_class_lang_rules"):
            return UncommissionedBanner(**kwargs)
    return _make


class TestTheWidgetNamesTheState:
    def test_the_banner_names_the_state_in_the_operators_words(self, banner):
        """One word, and the same word the provisioning run prints."""
        assert STATE_WORD == "UNCOMMISSIONED"
        assert banner().headline == STATE_WORD

    def test_the_detail_says_what_it_means_for_the_numbers_on_screen(self, banner):
        detail = banner().detail.lower()
        assert "default" in detail
        assert "not saved" in detail or "not be saved" in detail

    def test_the_strip_says_there_is_more_behind_it(self, banner):
        """A warning that happens to react to touch is a warning nobody
        touches. The remedies moved into the modal on 2026-09-22, so the strip
        has to advertise that the modal is there -- otherwise the operator's
        only way out is one they are never told about."""
        assert banner().hint.strip() != ""

    def test_the_strip_carries_no_remedy_of_its_own(self, banner):
        """Where the remedies went, pinned so a future edit does not quietly
        put a truncated one back on the strip.

        Both ways out need a paragraph each -- the restore names SSH and the
        restart, the dismissal has to say what it costs -- and a `shorten:
        True` label on a 1024px strip is where a paragraph goes to become an
        ellipsis. tests/components/test_uncommissioned_details.py is where the
        wording is now asserted.
        """
        assert not hasattr(banner(), "remedy")


class TestItTakesTheScreenOnlyWhenItShould:
    """Executed, not read: this is the half that must not silently misbehave."""

    def test_an_uncommissioned_machine_gets_a_visible_strip(self, banner):
        strip = banner()
        strip.active = True
        assert strip.opacity == 1
        assert strip.height > 0
        assert strip.disabled is False

    def test_a_commissioned_machine_sees_nothing(self, banner):
        strip = banner()
        strip.active = True
        strip.active = False
        assert strip.opacity == 0
        assert strip.height == 0

    def test_it_starts_collapsed(self, banner):
        """`active` defaults False, so a widget built before app.uncommissioned
        is bound does not flash a banner at a commissioned machine."""
        strip = banner()
        assert strip.active is False
        assert strip.height == 0
        assert strip.opacity == 0

    def test_a_collapsed_strip_swallows_no_touches(self, banner):
        """Zero opacity is not zero interaction: an invisible widget across the
        top of the home screen that eats a tap is a fault nobody at the machine
        could diagnose."""
        strip = banner()
        assert strip.disabled is True

    def test_the_height_it_takes_is_the_one_the_module_declares(self, banner):
        from kivy.metrics import dp
        strip = banner()
        strip.active = True
        assert strip.height == dp(BANNER_HEIGHT_DP)


class TestTappingItIsTheOnlyWayToTheModal:
    """Added 2026-09-22 with the shrink. The strip stopped carrying the
    remedies, so the tap is now load-bearing: if it does not open the modal
    the operator is left with one word and no way to act on it."""

    def test_the_strip_is_a_button(self, banner):
        """Not a decoration that a future kv edit could make tappable: the
        behaviour is in the class, where the collapse can disable it."""
        from kivy.uix.behaviors import ButtonBehavior
        assert isinstance(banner(), ButtonBehavior)

    def test_a_tap_opens_the_details_modal(self, banner):
        strip = banner()
        strip.active = True
        with patch("reflex.components.home.uncommissioned_details.open_details") as opened:
            strip.dispatch("on_release")
        opened.assert_called_once()

    def test_a_collapsed_strip_cannot_open_anything(self, banner):
        """A real touch, through the real dispatch path, on a collapsed strip.

        The zero-height widget still COLLIDES with a touch at its own origin,
        so this is not vacuous: something has to refuse it, and asserting on
        the outcome rather than on the mechanism means it stays a test if the
        mechanism changes.
        """
        strip = banner()
        assert strip.disabled is True
        touch = MagicMock()
        touch.x, touch.y, touch.pos = 0, 0, (0, 0)
        touch.is_mouse_scrolling = False
        touch.ud = {}
        with patch("reflex.components.home.uncommissioned_details.open_details") as opened:
            strip.on_touch_down(touch)
            strip.on_touch_up(touch)
        opened.assert_not_called()

    def test_there_is_no_dismiss_affordance_on_the_strip_itself(self):
        """A warning with several ways to silence it silences itself. The
        modal is the ONE route: no swipe, no long-press, no button in the kv."""
        text = BANNER_KV.read_text(encoding="utf-8").lower()
        for forbidden in ("on_long_press", "swipe", "on_touch_move", "button:"):
            assert forbidden not in text, (
                f"the strip grew a {forbidden!r} affordance; dismissal belongs "
                f"to the modal alone")

    def test_a_confirmed_dismissal_collapses_the_strip(self, banner):
        """The banner and the write gate are the same fact. If the strip stayed
        up after the gate opened the screen would be lying about the machine."""
        strip = banner()
        strip.active = True
        strip._gate_opened()
        assert strip.active is False
        assert strip.height == 0
        assert strip.opacity == 0

    def test_the_dismissal_callback_updates_the_app_property_too(self, banner):
        """One answer, two readers: app.uncommissioned is what the kv binds and
        commissioning_state.latched() is what the save gate reads. Collapsing
        only the widget would leave them disagreeing for the rest of the
        session."""
        strip = banner()
        strip.active = True
        app = MagicMock()
        app.uncommissioned = True
        with patch("reflex.components.home.uncommissioned_banner.App"
                   ".get_running_app", return_value=app):
            strip._gate_opened()
        assert app.uncommissioned is False


class TestItIsActuallyWiredUp:
    """The cdb6b4c class of defect: correct at every layer, connected at none."""

    def test_the_home_screen_instantiates_the_banner(self):
        text = HOME_KV.read_text(encoding="utf-8")
        assert "UncommissionedBanner:" in text, (
            "home_screen.kv never instantiates UncommissionedBanner, so the "
            "state is decided in build() and shown to nobody")

    def test_the_banner_sits_in_the_bars_container(self):
        """Above the status bar and above every mode layout, which HomePage
        adds to the same container afterwards."""
        text = HOME_KV.read_text(encoding="utf-8")
        assert text.index("UncommissionedBanner:") > text.index("id: bars_container")

    def test_the_home_screen_module_imports_the_banner(self):
        """kv resolves the class through Kivy's Factory, which only knows it
        once its module has been imported -- and it must be imported BEFORE
        home_screen.kv is loaded."""
        source = HOME_PY.read_text(encoding="utf-8")
        assert "uncommissioned_banner import UncommissionedBanner" in source
        assert source.index("uncommissioned_banner") < source.index("load_kv(__file__)")

    def test_the_banner_is_bound_to_the_app_state(self):
        text = BANNER_KV.read_text(encoding="utf-8")
        assert "active: app.uncommissioned" in text, (
            "the banner never reads app.uncommissioned, so it cannot know "
            "which machine it is on")

    def test_every_displayed_property_the_banner_maintains_is_rendered(self):
        """Generalised the way test_statusbar_peak.py generalises its defect: a
        future string property added here and never put in the kv fails."""
        text = BANNER_KV.read_text(encoding="utf-8")
        for prop in ("headline", "detail", "hint"):
            assert f"root.{prop}" in text, (
                f"UncommissionedBanner maintains {prop} but its kv never "
                f"renders it")

    def test_the_app_publishes_the_state_the_kv_binds(self):
        """`app.uncommissioned` has to be a real property on MainApp, or the kv
        binding above silently never establishes."""
        from kivy.properties import BooleanProperty

        from reflex.app import MainApp
        assert isinstance(MainApp.uncommissioned, BooleanProperty)
