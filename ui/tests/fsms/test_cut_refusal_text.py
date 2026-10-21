"""Every cut refusal, as one family.

The take-up refusals (utils/devices.py) and the coupling check's strips
(fsms/els_ratio.py) render in the same notice strip, over the status gutter,
so they share one contract: at most 65 characters, measured with the longest
position name a gearbox table allows. test_els_cal and test_els_ratio each
guard their own half; this guards the family as the operator sees it, with
every member listed in one place.

They also share one style: each stands alone and says what happened, then
one action. There is no shared opening, no "abort", and no question form.
"""
import pytest

from reflex.fsms import els_ratio as R
from reflex.fsms.els_gear import MAX_NAME_LEN
from reflex.utils.devices import (ELS_TAKEUP_MESSAGES, ELS_TAKEUP_TIMEOUT_LATCHED,
                                  ELS_TAKEUP_UNKNOWN, ELS_TAKEUP_WRONG_WAY)

#: The strip's character budget (tests/fsms/test_els_cal.py explains the
#: 435 px it stands for).
STRIP_MAX_CHARS = 65

#: The longest name a gearbox table allows, in the widest letter.
WIDEST_NAME = "W" * MAX_NAME_LEN

FAMILY = {
    **{f"takeup.{code}": msg for code, msg in ELS_TAKEUP_MESSAGES.items()},
    "takeup.wrong_way": ELS_TAKEUP_WRONG_WAY,
    "takeup.timeout_latched": ELS_TAKEUP_TIMEOUT_LATCHED,
    "takeup.unknown": ELS_TAKEUP_UNKNOWN,
    "ratio.gear_change": R.gear_change_text(WIDEST_NAME),
    "ratio.decoupled": R.abort_text(R.DECOUPLED),
    "ratio.wrong": R.abort_text(R.RATIO_WRONG),
}


def test_the_family_is_complete():
    assert len(FAMILY) == 9


@pytest.mark.parametrize("key", sorted(FAMILY))
def test_every_cut_refusal_fits_the_strip(key):
    msg = FAMILY[key]
    assert len(msg) <= STRIP_MAX_CHARS, (
        f"{key} is {len(msg)} characters, over the {STRIP_MAX_CHARS} the "
        f"notice strip has room for between the status chips; it will "
        f"render on top of the phase-offset chip's text. Measure it with "
        f"previews/preview_takeup_text_widths.py.\n  {msg}")


@pytest.mark.parametrize("key", sorted(FAMILY))
def test_every_cut_refusal_stands_alone(key):
    msg = FAMILY[key]
    low = msg.lower()
    assert "abort" not in low, f"{key}: no shared opening\n  {msg}"
    assert "?" not in msg, f"{key}: say what happened, not a question\n  {msg}"
    assert "looks like" not in low, f"{key}: say what was detected\n  {msg}"
    assert msg.endswith("."), f"{key}: whole sentences\n  {msg}"
