"""The route to the operator-notice surface for code holding no app reference.

WHY THIS IS ITS OWN MODULE. Two callers need to say something to the operator
from places that deliberately do not know about the app: ServoDispatcher (a
plain dispatcher) and ElsBar (a component loaded while its own kv is still
initialising). Both would have to reach MainApp lazily and guard the same four
ways, and a defensive guard duplicated in two files is one that drifts -- the
copy nobody edits is the copy that stops working.

WHY NOT IN notices.py. That module's docstring makes a point of being a plain
Python object with no Window, no GL context and no Kivy property, which is what
makes its expiry policy testable in microseconds against a fake clock. A
MainApp lookup would take that away. The policy lives there; the app-reaching
lives here.
"""
from kivy.logger import Logger

log = Logger.getChild(__name__)


def notify_operator(message: str, severity: str) -> bool:
    """Post to the top status bar. Returns True iff the operator will see it.

    Safe to call from the board polling thread, by els_uic.notify's own
    contract: the notice queue is locked and a Kivy property assignment is
    atomic enough for a string.

    NEVER RAISES. Every caller is a guard or a watchdog -- code whose whole job
    is to be reliable when something else is already wrong -- and at the lathe
    a component that takes the app down is worse than one that cannot speak.
    A False return is the honest answer to "did anyone see it", which is what
    lets a test tell a posted notice apart from a swallowed one; callers that
    also log keep their log line as the fallback channel.
    """
    try:
        from reflex.app import MainApp
        app = MainApp.get_running_app()
        uic = getattr(app, "els_uic", None) if app is not None else None
        if uic is None:
            # Previews, tests and early startup have no controller. Not an
            # error, and not worth a log line of its own -- it is the normal
            # state of every headless context this code runs in.
            return False
        return bool(uic.notify(message, severity))
    except Exception as e:
        log.error(f"operator notice failed ({message!r}): {e}")
        return False
