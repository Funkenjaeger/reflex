import asyncio
from keke import ktrace
from kivy.base import EventLoop
from kivy.logger import Logger
log = Logger.getChild(__name__)


class _DropCutBufferCritical:
    """Drop Kivy's one unavoidable false CRITICAL.

    On Linux, kivy/core/clipboard/__init__.py probes xclip then xsel for X11
    cut-buffer support unless the Clipboard provider is already one of them.
    Ours is sdl2 -- the Clipboard works -- so the probe always runs, and this
    machine has no X server and neither binary, so it always fails at
    CRITICAL. There is no setting to skip it.

    Installing xclip would silence it by making the probe succeed with a
    provider that cannot function without a display, which is worse: a real
    capability check answering yes for a capability that is absent.

    This matches that single message only. If Kivy rewords it, the filter
    stops matching and the CRITICAL returns -- the right way for a
    string-dependent silence to fail.
    """

    PREFIX = "Cutbuffer: Unable to find any valuable Cutbuffer provider"

    def filter(self, record):
        try:
            return not record.getMessage().startswith(self.PREFIX)
        except Exception:
            return True


Logger.addFilter(_DropCutBufferCritical())


if __name__ == "__main__":
    from reflex.app import MainApp
    # Monkeypatch to add more trace events
    EventLoop.idle = ktrace()(EventLoop.idle)
    try:
        asyncio.run(MainApp().async_run())
    except KeyboardInterrupt:
        log.info("Exiting Application")
