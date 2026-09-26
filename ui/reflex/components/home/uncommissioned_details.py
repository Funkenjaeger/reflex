"""What the uncommissioned strip opens when the operator taps it.

A SHORT STATEMENT AND BUTTONS THAT DO THE THING (redesigned 2026-09-26). The
first version was three paragraphs of facts and two numbered "ways out" in a
``CustomPopup`` at 18sp. On the first fresh elspi card (reflex v1.2.0,
2026-09-26) Evan found it wrong three ways at the lathe's 1024x600:

1. "the text is overflowing terribly. too many words there."
2. It never named the likeliest case -- a new machine being set up for the
   first time -- let alone led with it. "a user who's not new to it won't be
   confused."
3. It only described restoring over SSH, and did not offer the in-app USB and
   gist restores at all, though both already existed under Setup > Backup.

So the state is said in two lines, and every way out is a button, in the order
an operator is most likely to need it:

* **New machine -- set it up** (primary). Opens the write gate
  (``commissioning_state.dismiss``) and goes to Setup. Its caption says what
  that costs, because the cost is why the gate was shut: from then on, what is
  typed is recorded as this machine's baseline.
* **Restore from USB stick** and **Restore from GitHub gist**. Each goes to
  Setup > Backup and starts that restore there, so the confirm dialog and the
  result land on the screen that owns them, where a retry is one tap away.
* **Not now**. Closes the dialog and changes nothing.

Restoring over SSH is not offered here. A technician doing that has a shell
and does not need a touchscreen button; it is on the guide page
(``docs/guide/uncommissioned.md``).

WHY THE NEW-MACHINE BUTTON IS ABSENT AFTER AN IMPORT, NOT GREYED.
``commissioning_bundle.apply`` writes restored YAML to disk while every
dispatcher in memory still holds its in-code defaults, so opening the gate then
lets the next property change write those defaults over the restore (see
``commissioning_state.dismissal_available``). The button has to stop working,
and it is removed rather than greyed because a control that is present and does
nothing is the defect ``tests/components/test_custom_popup.py`` was written
about: at a lathe with no terminal an inert button looks exactly like a broken
one. The restart branch says in words why it is gone.

Defence in depth, not instead of it: ``commissioning_state.dismiss()`` refuses
on its own account too, so a dialog left open while an import lands still
cannot open the gate.

THE OPTIONS ARE DATA (:func:`options_for`), and the buttons are built from them
in Python rather than written in the kv. Which buttons exist is the property
under test, and this repo's mock-GL test backend cannot build a kv tree that
carries a canvas (see ``uncommissioned_banner.py``), so the decision lives
where a test can run it and the kv only lays it out.
"""
from dataclasses import dataclass

from kivy.app import App
from kivy.factory import Factory
from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup

from reflex.components.widgets import facelift_chrome  # noqa: F401 -- defines <SetupButton>
from reflex.utils import commissioning_state, gist_sync
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

#: The dialog's title bar. Says the state again: the operator got here by
#: tapping the word UNCOMMISSIONED, and the title confirms they are in the
#: right place.
TITLE = "This machine is not commissioned"

#: The ordinary branch: what the state is, in two lines.
HEADLINE = "This card has no settings for this lathe yet."
LINE = "Until it does, the screen shows defaults and nothing is saved."

#: The restart branch: settings arrived on disk after the app started (an
#: in-app restore, or files copied over SSH), and only a restart loads them.
AFTER_IMPORT_HEADLINE = "Settings were restored to this card."
AFTER_IMPORT_LINE = "Restart the machine to load them."
#: Where the new-machine button went. Without this sentence the missing button
#: is just a bug.
AFTER_IMPORT_NOTE = (
    "Until the restart, defaults are shown, nothing is saved, and new-machine "
    "setup is off so the defaults cannot overwrite what was restored.")

NEW_MACHINE = "new_machine"
RESTORE_USB = "restore_usb"
RESTORE_GIST = "restore_gist"
NOT_NOW = "not_now"
RESTART_OK = "restart_ok"

#: The dialog's share of the window, per branch. The restart branch has one
#: button and needs far less height.
#: Sized to the content at 1024x600 (previews/preview_uncommissioned_options.py).
SIZE_HINT = (0.74, 0.84)
SIZE_HINT_RESTART = (0.74, 0.5)


@dataclass(frozen=True)
class Option:
    """One button in the dialog."""
    key: str
    text: str
    #: A second, smaller line on the button. Only the new-machine button has
    #: one: pressing it has a lasting consequence, and the place to say so is
    #: on the thing being pressed.
    caption: str = ""
    primary: bool = False


def options_for(*, can_dismiss: bool, gist_available: bool) -> tuple[Option, ...]:
    """The buttons, in order.

    :param can_dismiss: ``commissioning_state.dismissal_available()``. False
        after an import, when the only honest option is a restart.
    :param gist_available: ``gist_sync.is_configured()``. A build without a
        GitHub client id cannot restore from a gist, so that button is left
        out, by the same absent-not-greyed rule as above.
    """
    if not can_dismiss:
        return (Option(RESTART_OK, "OK"),)
    options = [
        Option(NEW_MACHINE, "New machine — set it up",
               caption="Starts saving. Enter this lathe's measured values in Setup.",
               primary=True),
        Option(RESTORE_USB, "Restore from USB stick"),
    ]
    if gist_available:
        options.append(Option(RESTORE_GIST, "Restore from GitHub gist"))
    options.append(Option(NOT_NOW, "Not now — keep defaults, save nothing"))
    return tuple(options)


def button_text(option: Option) -> str:
    """What goes on the button: the caption, when there is one, as a smaller
    second line in Kivy markup."""
    if not option.caption:
        return option.text
    return f"{option.text}\n[size=15sp]{option.caption}[/size]"


class UncommissionedOptions(BoxLayout):
    """The dialog's content. Wrapped in a stock ``Popup``, the way
    ``CustomPopup`` wraps itself, so it gets the app's popup chrome."""

    headline = StringProperty(HEADLINE)
    line = StringProperty(LINE)
    #: A third, smaller paragraph. Empty, and zero height, in the ordinary
    #: branch.
    note = StringProperty("")

    def __init__(self, *, can_dismiss: bool, gist_available: bool,
                 on_dismissed=None, **kwargs):
        super().__init__(**kwargs)
        self.on_dismissed_cb = on_dismissed
        self.options = options_for(can_dismiss=can_dismiss,
                                   gist_available=gist_available)
        if not can_dismiss:
            self.headline = AFTER_IMPORT_HEADLINE
            self.line = AFTER_IMPORT_LINE
            self.note = AFTER_IMPORT_NOTE
        self.buttons = {}
        column = self.ids.get("options", self)
        for option in self.options:
            factory = (Factory.UncommissionedPrimaryButton if option.primary
                       else Factory.UncommissionedOptionButton)
            button = factory(text=button_text(option))
            button.bind(on_release=lambda _b, key=option.key: self.choose(key))
            self.buttons[option.key] = button
            column.add_widget(button)
        self._popup = Popup(
            title=TITLE,
            content=self,
            size_hint=SIZE_HINT if can_dismiss else SIZE_HINT_RESTART,
            auto_dismiss=False,
        )

    def open(self):
        self._popup.open()

    def close(self):
        self._popup.dismiss()

    def choose(self, key: str) -> None:
        """What each button does. One method, so a test can press any button
        by name, and an unknown key is a loud error rather than a dead button."""
        if key in (NOT_NOW, RESTART_OK):
            self.close()
        elif key == NEW_MACHINE:
            self._new_machine()
        elif key == RESTORE_USB:
            self.close()
            backup = _goto_backup()
            if backup is not None:
                backup.import_from_usb()
        elif key == RESTORE_GIST:
            self.close()
            backup = _goto_backup()
            if backup is not None:
                backup.restore_from_gist()
        else:
            raise ValueError(f"uncommissioned options: unknown choice {key!r}")

    def _new_machine(self) -> None:
        """Open the gate, then go to Setup -- only if it actually opened.

        ``dismiss()`` re-checks availability itself. If an import landed while
        this dialog sat open it refuses, and the dialog is replaced by the
        restart branch, which says why, rather than closing on nothing.
        """
        self.close()
        if not commissioning_state.dismiss():
            log.warning("uncommissioned options: dismissal refused; showing the restart branch")
            open_details(on_dismissed=self.on_dismissed_cb)
            return
        if self.on_dismissed_cb is not None:
            self.on_dismissed_cb()
        manager = _manager()
        if manager is not None:
            manager.goto("setup")


def _manager():
    return getattr(App.get_running_app(), "manager", None)


def _goto_backup():
    """Show Setup > Backup and return it, so the caller can start a restore
    there. ``None`` off a running app (previews, tools)."""
    manager = _manager()
    if manager is None:
        log.warning("uncommissioned options: no screen manager; cannot open Backup")
        return None
    manager.goto("backup")
    return manager.get_screen("backup")


def open_details(on_dismissed=None) -> UncommissionedOptions:
    """Build and open the dialog for whatever state this process is in."""
    options = UncommissionedOptions(
        can_dismiss=commissioning_state.dismissal_available(),
        gist_available=gist_sync.is_configured(),
        on_dismissed=on_dismissed,
    )
    options.open()
    return options
