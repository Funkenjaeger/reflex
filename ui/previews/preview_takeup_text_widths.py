"""Measure candidate take-up warning texts against the space they really have.

THE GAP IS NOT THE BUDGET. previews/preview_phase_offset.py's first pass at
this compared each message's width against the space BETWEEN the two status
chips (586 px) and reported every one as fitting -- and the render then showed
the longest one sitting on top of the phase chip's text anyway. The notice
strip's Label is `halign: 'center'` across the FULL bar, and the gap is not
centred on the bar: chip_reference ends at 197 and chip_phase begins at 783,
while the bar's own centre is 565. So a centred string is bounded by TWICE its
distance to the nearer obstruction, not by the gap.

That is the number this script reports, for whatever candidate strings are
listed below, so the texts are chosen from a measurement instead of from a
character count. Run (WSL):

    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" \\
        ./.venv/bin/python previews/preview_takeup_text_widths.py
"""
import os
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-takeup-widths-")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from kivy.config import Config  # noqa: E402

Config.set("graphics", "width", "1024")
Config.set("graphics", "height", "600")

import reflex  # noqa: E402
from kivy.resources import resource_add_path  # noqa: E402

resource_add_path(os.path.dirname(reflex.__file__))

from kivy.core.text import Label as CoreLabel  # noqa: E402
from kivy.metrics import dp  # noqa: E402

from reflex.components.widgets.palettes import PALETTES  # noqa: E402
from reflex.utils.devices import (ELS_TAKEUP_MESSAGES,  # noqa: E402
                                  ELS_TAKEUP_WRONG_WAY,
                                  ELS_TAKEUP_UNKNOWN)

# Measured off the live layout by previews/preview_phase_offset.py, stop-only
# with both chips up at 1024x600. RESTATED, not read live: this script exists
# to compare candidate STRINGS quickly and does not stand the app up. If the
# chips or the bar change shape, re-run preview_phase_offset.py first and
# copy its numbers here -- it prints them, and it is the authority.
BAR_X, BAR_W = 107, 917
CHIP_REF_RIGHT = 197
CHIP_PHASE_X = 783
BAR_CENTRE = BAR_X + BAR_W / 2.0
# A centred string may extend only as far as the NEARER obstruction, both ways.
CENTRED_BUDGET = 2 * min(BAR_CENTRE - CHIP_REF_RIGHT, CHIP_PHASE_X - BAR_CENTRE)
GAP = CHIP_PHASE_X - CHIP_REF_RIGHT

FONT = os.path.join(os.path.dirname(reflex.__file__),
                    PALETTES["dark"]["font_bold"])

CANDIDATES = {
    "SERVOMODE (current)": ELS_TAKEUP_MESSAGES[2],
    "SERVOMODE alt A": "Cut aborted — servo in jog mode. Leave jog and press Cut again.",
    "SERVOMODE alt B": "Cut aborted — servo is in jog mode. Leave jog, then Cut.",
    "SERVOMODE alt C": "Cut aborted — servo in jog mode. Leave jog to cut.",
    "UNCONFIRMED (current)": ELS_TAKEUP_MESSAGES[4],
    "TIMEOUT (current)": ELS_TAKEUP_MESSAGES[6],
    "TIMEOUT alt A": "Cut aborted — take-up did not complete. Disengage and re-engage.",
    "TIMEOUT alt B": "Cut aborted — take-up did not complete. Re-engage the ELS stop.",
    "TIMEOUT alt C": "Cut aborted — take-up did not complete. Disengage, then engage.",
    "TIMEOUT alt D": "Cut aborted — take-up did not complete. Re-engage to clear.",
    "WRONG_WAY (current)": ELS_TAKEUP_WRONG_WAY,
    "WRONG_WAY alt A": "Cut aborted — carriage moved the WRONG way. Check Z scale direction.",
    "WRONG_WAY alt B": "Cut aborted — carriage moved the WRONG way. Check the Z scale.",
    "WRONG_WAY alt C": "Cut aborted — WRONG-way carriage motion. Check Z scale direction.",
    "WRONG_WAY alt D": "Cut aborted — carriage moved WRONG way. Check Z scale direction.",
    "WRONG_WAY alt E": "Cut aborted — WRONG-way motion. Check the Z scale direction.",
    "WRONG_WAY alt F": "Cut aborted — carriage ran the WRONG way. Check Z scale direction.",
    "TIMEOUT alt E": "Cut aborted — take-up did not complete. Re-engage the stop.",
    "SERVOMODE alt D": "Cut aborted — the servo is in jog mode. Leave jog, then Cut.",
    "UNKNOWN (current)": ELS_TAKEUP_UNKNOWN,
}


def width(text):
    cl = CoreLabel(text=text, font_name=FONT, font_size=dp(13))
    cl.refresh()
    return cl.texture.size[0]


print(f"bar {BAR_X}..{BAR_X + BAR_W}, centre {BAR_CENTRE:.0f}")
print(f"chip_reference.right={CHIP_REF_RIGHT}  chip_phase.x={CHIP_PHASE_X}")
print(f"raw gap {GAP:.0f} px, but a CENTRED string only gets "
      f"{CENTRED_BUDGET:.0f} px")
print()
for name, text in CANDIDATES.items():
    w = width(text)
    verdict = "fits" if w <= CENTRED_BUDGET else f"OVER by {w - CENTRED_BUDGET:.0f}"
    print(f"  {name:<24} {len(text):>3} ch  {w:>4.0f} px  "
          f"{w / len(text):.2f} px/ch   {verdict}")
print()
print(f"  {'char budget at worst density':<24} "
      f"{CENTRED_BUDGET / 6.7:.0f} chars")
