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
been the other option and is worse twice over -- it needs a way OUT, and it
would have to be suppressed on every subsequent screen anyway, which is the
banner with extra steps.

WHY IT MAY TAKE SPACE WHEN THE NOTICE STRIP MAY NOT. ``notices.py`` and
``statusbar.kv`` carry a standing rule from Evan (2026-08-22): "having things
resize around a temporary warning is distracting", so a transient notice
covers and never resizes. This is not a transient notice. It is decided ONCE,
in ``MainApp.build``, before a single widget exists. Nothing resizes around it
while the app runs, because it is either there for the whole session or not
there at all -- with one exception, added deliberately and discussed below.

ONE LINE, AND WHY IT SHRANK (2026-09-22). It used to be 52dp and two rows: a
headline, an elaboration, and a remedy line, the last two both ``shorten:
True``. At the lathe's 1024x600 that meant the two rows carrying the actual
information were each one ellipsis away from saying nothing, while the strip
charged the DRO 52dp of screen for the privilege -- permanently, on a machine
that may sit uncommissioned for days while somebody finds time to measure it.
The state is one word and it stays on the screen; everything that needs a
paragraph moved behind a tap, into ``uncommissioned_details``, where it has
room to be read and where the SECOND way out (dismissal) could finally be
stated at all.

WHY TAPPABLE IS NOT THE THING THE OLD COMMENT WARNED ABOUT. This file used to
argue that "a button that dismisses it is a button that makes the warning
optional, and on a touchscreen that collects accidental taps it is a warning
that dismisses itself". That is still true of a dismiss button ON THE STRIP,
and there is still not one. Tapping the strip OPENS A MODAL; dismissal is a
second, deliberate press against a screen of prose explaining what it costs.
An accidental tap at the top of the home screen gets an explanation, which is
the correct outcome of an accidental tap on a warning. There is deliberately
no other route: no swipe, no long-press, no Setup toggle. A warning with
several ways to silence it is a warning that silences itself.

WHY THE STRIP CAN NOW DISAPPEAR MID-SESSION. Exactly one event removes it: the
operator confirming dismissal in that modal, which is also the event that opens
the write gate (``commissioning_state.dismiss``). The banner and the gate are
the same fact and they change together or the screen is lying. The layout shift
is the acknowledgement of a deliberate press, not a thing that happens around
the operator.
"""
from kivy.app import App
from kivy.metrics import dp
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

load_kv(__file__)

#: The one word the operator has to come away with. Deliberately a module
#: constant: the test that proves the banner NAMES the state asserts on this,
#: and the provisioning run's own banner uses the same word.
STATE_WORD = "UNCOMMISSIONED"

#: One line. Down from 52dp when the strip carried three rows of prose.
#:
#: The touch target is the FULL WIDTH of the home screen, so the dimension
#: under pressure is this one, and 36dp on the lathe's panel is comfortably
#: past the ~9mm a gloved fingertip needs for a non-critical control. It is
#: also the only route to the modal, which is why it is not smaller.
BANNER_HEIGHT_DP = 36


class UncommissionedBanner(ButtonBehavior, BoxLayout):
    """A persistent, tappable strip across the top of the home screen.

    ``active`` is bound to ``app.uncommissioned`` in kv, which
    :meth:`reflex.app.MainApp.latch_commissioning_state` sets. It is also a
    plain property with a default of ``False``, so the widget can be built and
    inspected in a test without an application.

    NO ``BeepMixin``, unlike every other tappable in ``toolbars/``. Two
    reasons, and the second is the one that matters: a beep is the app
    acknowledging a CONTROL, and this is a warning being read rather than a
    machine function being commanded; and ``BeepMixin.on_press`` calls
    ``MainApp.get_running_app().beep()`` unconditionally, which would make the
    one newly-exercisable behaviour on this widget -- the tap -- untestable
    outside a running app.

    WHY THE COLLAPSE IS IN PYTHON AND NOT IN THE kv. It could have been two kv
    expressions (``height: dp(36) if root.active else 0`` and the same for
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

    #: What it means for the numbers on screen, and what the app is doing --
    #: the short form. The long form, and both remedies, are in the modal.
    detail = StringProperty("defaults, not this machine — settings are not saved")

    #: That there is more, and that it is reachable. Without this the strip is
    #: a notice that happens to react to touch, and nobody touches a notice.
    hint = StringProperty("Tap for options")

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
        home screen is a fault nobody could diagnose at the machine. Now that
        the strip IS a button, `disabled` is also what stops a commissioned
        machine opening a modal about a state it is not in.
        """
        self.size_hint_y = None
        self.height = dp(BANNER_HEIGHT_DP) if self.active else 0
        self.opacity = 1 if self.active else 0
        self.disabled = not self.active

    def on_release(self):
        """The only route to the explanation, and to dismissal.

        The ``active`` guard is belt to ``disabled``'s braces: a collapsed
        strip is still a widget at the top of the home screen, and a modal
        about a state the machine is not in would be worse than the swallowed
        tap ``_apply_active`` already guards against.
        """
        if not self.active:
            return
        from reflex.components.home.uncommissioned_details import open_details
        open_details(on_dismissed=self._gate_opened)

    def _gate_opened(self):
        """The gate opened; the screen must stop saying it is shut.

        NOT named ``on_dismissed``: on a Kivy ``EventDispatcher`` the ``on_``
        prefix is the event-handler namespace, and a plain method sitting in it
        reads as an event that was never registered.

        Writes ``app.uncommissioned`` rather than only collapsing itself, so
        the app property and ``commissioning_state.latched()`` keep agreeing --
        one answer, two readers, which is the property
        ``test_uncommissioned_startup_state`` pins. Setting ``active``
        afterwards is not redundant: off a running app (tests, previews) the kv
        binding that would have propagated it does not exist.
        """
        app = App.get_running_app()
        if app is not None and hasattr(app, "uncommissioned"):
            app.uncommissioned = False
        self.active = False
