"""The strip that says this lathe has never been measured.

WHY A VISIBLE THING AND NOT A LOG LINE. There is no terminal at this machine
(``reflex-real-machine-elspi``): a log line is a message to whoever reads the
file next week, and the operator standing at the lathe is the person who has to
know. The failure this guards against is precisely the one that LOOKS fine --
a freshly flashed card presenting a working lathe UI on ELS geometry nobody
measured -- so the state has to be said on the screen or it is not said.

WHY A STATE ON THE STARTUP PATH RATHER THAN A NEW SCREEN. The home screen is
where the app lands, so a strip at the top of it is seen without anyone
navigating anywhere, and it stays seen: every mode layout swaps INSIDE
``bars_container`` beneath it. A dedicated "not commissioned" screen would have
been the other option and is worse twice over -- it needs a way OUT (a button
that dismisses it is a button that makes the warning optional, and on a
touchscreen that collects accidental taps it is a warning that dismisses
itself), and it would have to be suppressed on every subsequent screen anyway,
which is the banner with extra steps.

WHY IT MAY TAKE SPACE WHEN THE NOTICE STRIP MAY NOT. ``notices.py`` and
``statusbar.kv`` carry a standing rule from Evan (2026-08-22): "having things
resize around a temporary warning is distracting", so a transient notice
covers and never resizes. This is not a transient notice. It is decided ONCE,
in ``MainApp.build``, before a single widget exists, and it cannot change while
the app runs -- ``commissioning_state`` latches it deliberately and has no
re-latch path. Nothing resizes around it, because it is either there for the
whole session or not there at all. It is closer to the two persistent ELS
overlays, which the same rule explicitly allows a placement of their own.

WHAT IT SAYS AND WHY THAT WORDING. Three facts the operator needs, in the order
they need them: what state the machine is in (the word UNCOMMISSIONED, which is
the same word the provisioning run prints), what that means for the numbers in
front of them (defaults, not this lathe), and what the app is therefore doing
about it (refusing to save, so nothing they touch is recorded as the
commissioning baseline). The remedy names the restore and the restart, because
restoring a capture while the app is running leaves every dispatcher holding
defaults in memory -- see ``commissioning_state.clear_latch``'s docstring.
"""
from kivy.metrics import dp
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

load_kv(__file__)

#: The one word the operator has to come away with. Deliberately a module
#: constant: the test that proves the banner NAMES the state asserts on this,
#: and the provisioning run's own banner uses the same word.
STATE_WORD = "UNCOMMISSIONED"

#: Tall enough for the headline row plus the remedy line. Only ever applied
#: when `active`; a commissioned machine gets zero.
BANNER_HEIGHT_DP = 52


class UncommissionedBanner(BoxLayout):
    """A persistent strip across the top of the home screen.

    ``active`` is bound to ``app.uncommissioned`` in kv, which
    :meth:`reflex.app.MainApp.latch_commissioning_state` sets. It is also a
    plain property with a default of ``False``, so the widget can be built and
    inspected in a test without an application.

    WHY THE COLLAPSE IS IN PYTHON AND NOT IN THE kv. It could have been two kv
    expressions (``height: dp(52) if root.active else 0`` and the same for
    ``opacity``), and that is where a Kivy reviewer would expect it. It is here
    because a kv expression cannot be EXERCISED in this repo's test
    environment: ``conftest.py`` forces ``KIVY_GL_BACKEND=mock``, and building
    any kv rule tree that carries a canvas segfaults the interpreter under it
    (verified while writing ``tests/components/test_uncommissioned_banner.py``
    -- ``Builder._build_canvas`` -> texture populate, hard crash). Everything
    the kv holds can therefore only ever be checked by reading it as TEXT, the
    way ``tests/components/test_statusbar_peak.py`` says and for the same
    reason. Whether the banner actually disappears on a commissioned machine
    is too load-bearing for a text match -- so the rule that decides it lives
    where a test can run it, and the kv keeps only the app binding and the
    paint.
    """

    #: Mirrors ``app.uncommissioned``. False collapses the strip to zero height
    #: and zero opacity -- on a commissioned machine the home screen is byte
    #: for byte the layout it was before this existed.
    active = BooleanProperty(False)

    #: The state, in one word.
    headline = StringProperty(STATE_WORD)

    #: What it means for the numbers on screen, and what the app is doing.
    detail = StringProperty(
        "No measured configuration on this card. Values shown are defaults, "
        "not this machine, and settings will NOT be saved."
    )

    #: The way out, naming the restart -- see the module docstring.
    remedy = StringProperty(
        "Restore a commissioning capture, then restart the machine."
    )

    def __init__(self, **kv):
        super().__init__(**kv)
        # After super(), because applying the class's kv rule is what sets
        # `active` from `app.uncommissioned` in the first place.
        self._apply_active()

    def on_active(self, *_args):
        self._apply_active()

    def _apply_active(self):
        """Take the space, or take none of it.

        `disabled` as well as `opacity`: a zero-opacity widget still takes
        touches, and an invisible strip that swallows a tap at the top of the
        home screen is a fault nobody could diagnose at the machine.
        """
        self.size_hint_y = None
        self.height = dp(BANNER_HEIGHT_DP) if self.active else 0
        self.opacity = 1 if self.active else 0
        self.disabled = not self.active
