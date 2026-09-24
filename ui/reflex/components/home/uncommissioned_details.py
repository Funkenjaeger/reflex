"""What the uncommissioned strip says when the operator taps it.

WHY A MODAL AT ALL, GIVEN THE STRIP USED TO SAY EVERYTHING. It said three
facts and a remedy across two fixed lines, both ``shorten: True`` -- so on the
lathe's 1024x600 the elaboration and the remedy were each one ellipsis away
from saying nothing, and the strip cost 52dp of the home screen permanently to
do it. Moving the prose behind a tap buys room to say it properly (and to say
the SECOND way out, which never fitted at all) and gives the strip back to the
DRO. The state itself -- the one word -- never moves off the screen.

WHY ``CustomPopup`` AND NOT A NEW POPUP CLASS. This repo already has exactly
one text-plus-buttons dialog and every call site uses it the same way, inline:
``els_advbar`` for "Axis Not Configured", ``app`` for its startup notices,
``backup_screen`` for the import confirm this module's hazard comes from.
Writing a second one would be inventing a convention next to the existing one.
What lives here instead is the WORDING and the BRANCH -- the two things that
are load-bearing and that a test can read without building a canvas.

THE TWO EXITS, AND WHY BOTH ARE ALWAYS NAMED. An operator reading this is in
one of two situations and the modal cannot tell which: they have a capture for
this machine sitting on another box, or this lathe has never been measured by
anybody and they are about to do it. Naming only the restore leaves the second
operator with a warning they cannot act on and an app that will not save --
which is how a warning gets worked around instead of answered. Naming only the
dismissal invites the first operator to hand-enter a machine they already have
measured numbers for.

WHY THE DISMISS BUTTON IS ABSENT AFTER AN IMPORT AND NOT MERELY DISABLED.
``commissioning_bundle.apply`` writes restored YAML to disk while every
dispatcher in memory still holds its in-code defaults, so opening the gate then
lets the next property change overwrite the restore (see
``commissioning_state.dismissal_available``). The button has to stop working.
It is removed rather than greyed because a control that is present and does
nothing is the exact defect this codebase has already been bitten by and wrote
a test file about: the ELS "Enable Feed" confirm whose callback never fired
behaved identically to Cancel, and nobody could tell from the machine
(``tests/components/test_custom_popup.py``). At a lathe with no terminal, an
inert button is indistinguishable from a broken one. A button that is not there
and a sentence saying why is diagnosable from across the shop.

Defence in depth, not instead of it: ``commissioning_state.dismiss()`` refuses
on its own account too, so a future caller that builds this dialog wrong still
cannot open the gate at the wrong moment.
"""
from kivy.logger import Logger

from reflex.components.popups.custom_popup import CustomPopup
from reflex.utils import commissioning_state

log = Logger.getChild(__name__)

#: The dialog's own name. Says the state again rather than saying "Warning":
#: the operator arrived here by tapping the word UNCOMMISSIONED and the title
#: should confirm they are in the right place.
TITLE = "This machine is not commissioned"

#: THE THREE FACTS, in the order the operator needs them. Kept as their own
#: constant because they are identical in both branches -- the hazard changes
#: what can be DONE about the state, never what the state is.
FACTS = (
    "This card carries no measured configuration. Nothing on it was taken "
    "from this lathe.\n"
    "\n"
    "The values on screen are the app's built-in defaults -- somebody else's "
    "machine, near enough to look plausible and not near enough to cut with.\n"
    "\n"
    "Settings are not being saved. Anything you change now is forgotten when "
    "the app closes."
)

#: EXIT (a). The restore, and why it is permanent: the capture clears the
#: restore contract's bar, so the next boot finds a commissioned machine and
#: the strip does not come back. No marker, no dismissal, nothing to undo.
EXIT_RESTORE = (
    "1. RESTORE A CAPTURE (do this if one exists)\n"
    "   Provision or restore this machine's commissioning capture over SSH, "
    "then restart. The restored configuration is found on the next boot and "
    "this warning does not appear again."
)

#: EXIT (b). The hand-commissioning path. It says what dismissal COSTS in the
#: same breath as what it gives, because the cost is the whole reason the gate
#: was shut: from that moment the app records what is typed as this machine's
#: baseline, and nothing downstream can tell a measured number from a guess.
EXIT_DISMISS = (
    "2. DISMISS AND COMMISSION BY HAND\n"
    "   If there is no capture, press Dismiss. Saving switches on immediately "
    "and stays on for this card, through restarts. Work through Setup and "
    "enter this lathe's real measured values -- everything you save from then "
    "on is recorded as this machine's baseline, so measure it before you "
    "type it."
)

#: The post-import branch. Says what happened, why the button is gone, and the
#: one thing that fixes it. "Restart" is the remedy and the only remedy.
AFTER_IMPORT = (
    "A configuration was written to this card after the app started -- an "
    "in-app backup import, or a restore over SSH.\n"
    "\n"
    "Those files are on disk, but this app is still running on the defaults it "
    "started with. Switching saving on now would write the defaults back over "
    "what was just restored, so Dismiss is not offered until the machine has "
    "been restarted.\n"
    "\n"
    "RESTART THE MACHINE. The restored configuration is picked up on the next "
    "boot and this warning will not appear."
)


def details_message(*, can_dismiss: bool) -> str:
    """The modal's body text.

    :param can_dismiss: ``commissioning_state.dismissal_available()``. Taken as
        an argument rather than read here so the wording is a pure function of
        the branch and can be read in a test without a latched process.
    """
    if can_dismiss:
        return f"{FACTS}\n\nTwo ways out:\n\n{EXIT_RESTORE}\n\n{EXIT_DISMISS}"
    return f"{FACTS}\n\n{AFTER_IMPORT}"


def build_details_popup(*, can_dismiss: bool, on_dismissed=None) -> CustomPopup:
    """The dialog, built but not opened.

    Separate from :func:`open_details` so a test can inspect the buttons that
    were actually wired -- whether the dismiss affordance EXISTS is the
    property under test, and a function that opens a window cannot be asked.

    ``cancel_text`` empty is how ``CustomPopup`` renders a single-button
    dialog, so the no-dismiss branch is one OK button and nothing else: there
    is no second control for the operator to wonder about.
    """
    if not can_dismiss:
        return CustomPopup(
            title=TITLE,
            message=details_message(can_dismiss=False),
            button_text="OK",
            popup_size_hint=[0.85, 0.8],
        )
    return CustomPopup(
        title=TITLE,
        message=details_message(can_dismiss=True),
        button_text="Dismiss",
        cancel_text="Close",
        confirm_callback=lambda: _confirm_dismissal(on_dismissed),
        popup_size_hint=[0.85, 0.85],
    )


def _confirm_dismissal(on_dismissed) -> None:
    """Open the gate, and tell the caller only if it actually opened.

    ``dismiss()`` re-checks availability itself, so the window between
    building this dialog and pressing the button -- long enough for an import
    to land -- cannot be used to get the gate open.
    """
    if not commissioning_state.dismiss():
        log.warning("uncommissioned details: dismissal refused; banner stays")
        return
    if on_dismissed is not None:
        on_dismissed()


def open_details(on_dismissed=None) -> CustomPopup:
    """Build and open the dialog for whatever state this process is in."""
    popup = build_details_popup(
        can_dismiss=commissioning_state.dismissal_available(),
        on_dismissed=on_dismissed,
    )
    popup.open()
    return popup
