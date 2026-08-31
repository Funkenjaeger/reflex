"""The Cutbuffer CRITICAL filter drops one message and nothing else.

A log filter that silences a CRITICAL is exactly the kind of change that can
quietly widen. Kivy emits one unavoidable false CRITICAL on this machine --
the X11 cut-buffer probe, for a mechanism a KMS/DRM box with no X server
cannot have and this app never asks for -- and the filter exists for that
single line.

So the test that matters is not "does it drop the cutbuffer message" but
"does it drop anything else", and the answer must be no: not other CRITICALs,
not other Cutbuffer messages, not a message that merely contains the phrase
further in.
"""
import logging

import pytest

from reflex.main import _DropCutBufferCritical


def _record(msg, level=logging.CRITICAL, args=None):
    return logging.LogRecord(
        name="kivy", level=level, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )


@pytest.fixture
def filt():
    return _DropCutBufferCritical()


def test_drops_the_real_message(filt):
    """The exact line Kivy emits, verbatim from elspi's journal."""
    msg = ("Cutbuffer: Unable to find any valuable Cutbuffer provider. "
           "Please enable debug logging (e.g. add -d if running from the "
           "command line, or change the log level in the config) and re-run "
           "your app to identify potential causes")
    assert filt.filter(_record(msg)) is False


def test_drops_it_when_lazily_formatted(filt):
    """Kivy logs with %-args; the filter must resolve them, not read .msg."""
    rec = _record("Cutbuffer: Unable to find any valuable Cutbuffer provider%s",
                  args=(".",))
    assert filt.filter(rec) is False


@pytest.mark.parametrize("msg", [
    # Other CRITICALs must survive -- this is the whole risk of the change.
    "Window: Unable to find any valuable Window provider",
    "Clipboard: Unable to find any valuable Clipboard provider",
    "Cutbuffer: cut buffer support enabled",
    "Modbus: No communication with the instrument (no answer)",
    "ELS: take-up refused, carriage did not move",
    # The phrase must anchor at the start, not match mid-message: a report
    # ABOUT the suppression is not the suppressed line.
    "Suppressed: Cutbuffer: Unable to find any valuable Cutbuffer provider",
    "",
])
def test_keeps_everything_else(filt, msg):
    assert filt.filter(_record(msg)) is True


def test_keeps_a_record_whose_formatting_explodes(filt):
    """A malformed record must pass through, never be swallowed by the filter."""
    rec = _record("Cutbuffer: %d placeholders", args=("not an int",))
    assert filt.filter(rec) is True


def test_level_is_not_the_criterion(filt):
    """Matching is on the message. Kivy's level for this line is not a
    contract, and the filter should not start depending on one."""
    msg = "Cutbuffer: Unable to find any valuable Cutbuffer provider"
    assert filt.filter(_record(msg, level=logging.WARNING)) is False
    assert filt.filter(_record(msg, level=logging.INFO)) is False
