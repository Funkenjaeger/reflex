"""Keypad (reflex/components/popups/keypad.py) is SKIPPED for construction /
show_with_callback wiring tests -- see the reasoning below. This file exists
so the gap is a deliberate, documented decision rather than silent missing
coverage.

Unlike CustomPopup (which WRAPS a kivy Popup, so the whole real Popup can be
replaced with a MagicMock while the widget-under-test's own python wiring
stays real) and ElsSettingsPopup (whose interesting logic is a handful of pure
methods callable unbound against a bare stand-in), Keypad builds its entire
button/label layout as REAL Kivy widgets directly in __init__ -- KeypadButton
(a real kivy Button, i.e. a Label subclass with a themed background), a real
kivy.uix.label.Label for the value display, KeypadIconButton, etc. -- with no
kv file of its own to strip out via apply_class_lang_rules (Keypad never calls
load_kv).

Empirically verified while building this suite (`patch.object(Keypad,
"apply_class_lang_rules")` + a faked `MainApp.get_running_app()`, same pattern
as everywhere else in this file):

    Keypad()

crashes the interpreter with a Windows access violation (not a catchable
Python exception) inside Kivy's mock GL backend while populating a texture for
the child Label's kv-rule canvas:

    kivy/core/image/__init__.py:267 in populate
    kivy/core/image/__init__.py:316 in textures
    kivy/lang/builder.py:917 in _build_canvas
    kivy/uix/widget.py:470 in apply_class_lang_rules   (Label's OWN rule, not Keypad's)
    kivy/uix/label.py:319 in __init__
    reflex/components/popups/keypad.py:32 in __init__  (value_label = Label(...))

Patching Keypad.apply_class_lang_rules does nothing for this, because the
crash is in the CHILD Label's (unpatched, real) rule application, not
Keypad's own. Making Keypad constructible would require also neutralizing
Label/Button/KeypadButton/KeypadIconButton's real kv rules and graphics --
at which point the test exercises almost no real Keypad code, so it isn't
worth doing. ElsAdvancedBar's keypad wiring (tests/components/test_els_advbar.py)
covers the actual production risk this widget layer cares about: it patches
`reflex.components.popups.keypad.Keypad` wholesale and asserts on the
show_with_callback kwargs / callback invocations, so Keypad itself never gets
constructed there either.
"""
import pytest


@pytest.mark.skip(
    reason=(
        "Keypad() crashes the interpreter (Windows access violation) under the "
        "mock GL test backend -- a real child Label's kv rule triggers texture "
        "population. See this module's docstring for the verified traceback. "
        "Not a catchable exception, so no test can safely construct a real "
        "Keypad here; ElsAdvancedBar's keypad tests patch Keypad wholesale instead."
    )
)
def test_keypad_construction_not_covered_here():
    pass


# ── the sign key, tested UNBOUND ─────────────────────────────────────────
# Keypad cannot be constructed here (see the whole preamble above), but
# sign_key touches only self.nonnegative and self.ids['value'], so it runs
# perfectly well against a stand-in. That is enough to pin the RULE. The
# button's `disabled` flag cannot be reached without construction; the call
# site is pinned instead, in test_els_phase_offset_popup.py.
from types import SimpleNamespace          # noqa: E402

from reflex.components.popups.keypad import Keypad   # noqa: E402


def _stub(text, nonnegative):
    return SimpleNamespace(nonnegative=nonnegative,
                           ids={"value": SimpleNamespace(text=text)})


def test_sign_key_is_inert_on_a_nonnegative_field():
    """Guarded as well as hidden. A disabled button is a look, not a rule --
    a hardware keyboard's minus reaches the same method."""
    stub = _stub("0.005", nonnegative=True)

    Keypad.sign_key(stub)

    assert stub.ids["value"].text == "0.005"


def test_sign_key_cannot_strip_a_sign_on_a_nonnegative_field_either():
    """Inert means inert, not 'only refuses to ADD a minus'."""
    stub = _stub("-0.005", nonnegative=True)

    Keypad.sign_key(stub)

    assert stub.ids["value"].text == "-0.005"


def test_sign_key_still_toggles_where_negatives_are_legal():
    """The failure mode of an over-eager guard is a keypad that can no longer
    enter a negative anywhere in the app."""
    stub = _stub("12.5", nonnegative=False)

    Keypad.sign_key(stub)
    assert stub.ids["value"].text == "-12.5"

    Keypad.sign_key(stub)
    assert stub.ids["value"].text == "12.5"
