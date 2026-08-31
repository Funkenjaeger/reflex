"""Generate docs/guide/when-it-refuses.md from the message tables themselves.

WHY THIS IS GENERATED. Every other page in the guide is prose, written once and
edited by hand. This one is a catalogue of exact strings the operator sees on
screen, and a catalogue transcribed by hand is a catalogue that is wrong within
a release -- the take-up messages were rewritten on 2026-08-29 and again on
2026-08-30, and a hand-copied page would still be showing the first version.

THE ANNOTATIONS ARE THE PART A HUMAN WRITES, and every message must have one:
this script FAILS if a message exists with no "what to do" text, so adding a
refusal to the code forces someone to say what an operator should do about it
before the docs will build.

Run (from ui/, which is where the venv and the package live):

    ./.venv/bin/python ../tools/gen_refusal_catalogue.py

Verify without writing (what CI does):

    ./.venv/bin/python ../tools/gen_refusal_catalogue.py --check
"""
import os
import sys

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_WINDOW", "mock")
os.environ.setdefault("KIVY_GL_BACKEND", "mock")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "ui"))

from reflex.components.home.els_phase_offset_popup import REFUSAL_TEXT  # noqa: E402
from reflex.fsms.els_fsm import ElsFsm  # noqa: E402
from reflex.utils.devices import (  # noqa: E402
    ELS_CAL_ERR_ABORTED,
    ELS_CAL_ERR_CONFIG,
    ELS_CAL_ERR_ENABLED,
    ELS_CAL_ERR_NO_MOTION,
    ELS_CAL_ERR_SERVOMODE,
    ELS_CAL_MESSAGES,
    ELS_TAKEUP_ERR_TIMEOUT,
    ELS_TAKEUP_ERR_UNCONFIRMED,
    ELS_TAKEUP_MESSAGES,
    ELS_TAKEUP_TIMEOUT_LATCHED,
    ELS_TAKEUP_UNKNOWN,
    ELS_TAKEUP_WRONG_WAY,
)

OUT = os.path.join(REPO, "docs", "guide", "when-it-refuses.md")

# ── the human half ─────────────────────────────────────────────────────
# Keyed by a stable id, NOT by the message text -- keying by text would make
# every reword look like a new refusal and silently drop the annotation.
ADVICE = {
    "takeup.unconfirmed": (
        "The controller drove the leadscrew through its backlash and the Z "
        "scale did not move, so the carriage did not. **Close the half nut and "
        "press Cut again** — that is the whole recovery, and it is the fault "
        "this check exists to catch. If the half nut *was* closed, look for a "
        "slipping coupling, a disconnected Z scale, or a servo that is not "
        "enabled."),
    "takeup.wrong_way": (
        "The carriage moved, but the **wrong way**. This is a wiring or "
        "scale-direction fault, not a matter of not moving far enough, and it "
        "will corrupt every other ELS operation the same way. Check the Z "
        "scale direction in setup before doing anything else."),
    "takeup.servomode": (
        "The servo is in jog mode, where the take-up is never consumed. "
        "**Leave jog and press Cut again.** Nothing is wrong with the "
        "machine."),
    "takeup.timeout": (
        "The take-up was commanded and never reached its target — the usual "
        "cause is the servo not being in sync mode at all. **Pressing Cut "
        "again will not clear this**: the motion gate stays shut until the ELS "
        "stop is disengaged and re-engaged."),
    "takeup.timeout_latched": (
        "The same fault, shown when you have a thread reference to lose. The "
        "remedy is forced — only the disengage/re-engage cycle releases the "
        "gate — and that cycle starts a new job, which clears the thread "
        "reference and any phase offset. There is no way around it; the "
        "message exists so it is not a surprise."),
    "takeup.unknown": (
        "The controller reported a take-up result this screen does not "
        "recognise, which usually means the UI is older than the firmware. "
        "Check the versions match."),
    "cal.enabled": (
        "A threading job is armed. **Disengage first** — but note the cost: "
        "re-engaging afterwards starts a new job, which clears the thread "
        "reference and any phase offset. Finish the thread before "
        "calibrating."),
    "cal.servomode": (
        "The servo is not in sync/index mode. The calibration normally puts it "
        "there itself, so seeing this means the mode could not be set — check "
        "the link and the servo's own state."),
    "cal.config": (
        "The calibration's limits are not configured. Set the ceiling and the "
        "motion threshold in setup; a zero motion threshold makes the firmware "
        "fail closed and is never a usable default."),
    "cal.no_motion": (
        "The calibration drove its full ceiling and the carriage never moved. "
        "Same physical fault as a refused take-up, and the same first check: "
        "**is the half nut engaged?**"),
    "cal.aborted": (
        "Conditions changed while the calibration was running — the ELS stop "
        "was engaged, or the servo mode changed. Re-run it undisturbed."),
    "offset.offline": (
        "The link to the controller is down, so there is nothing holding the "
        "offset. Reconnect and try again; nothing was applied."),
    "offset.no_job": (
        "No threading job is engaged. The controller discards a phase offset "
        "when a job starts, so it declines to hold one that would evaporate. "
        "Engage the ELS stop first."),
    "offset.no_pitch": (
        "Turning has no thread phase to shift. Choose a threading mode and a "
        "pitch before setting an offset."),
    "offset.no_geometry": (
        "The servo gearing that converts a distance into leadscrew steps is "
        "missing or zero, so the offset cannot be expressed in steps. Fix the "
        "gearing in setup — and note that everything else the ELS commands is "
        "wrong by the same factor until you do."),
    "offset.negative": (
        "A minus sign does not back the phase up: it becomes a forward move "
        "that opens the *wrong* side of the groove. Enter the distance without "
        "the sign, or press Clear."),
    "offset.at_pitch": (
        "A full pitch lands back in the groove you started at, so it is not a "
        "widening at all. Type a smaller offset, or press Clear. Note the "
        "field is **absolute** — the whole offset from the original groove, "
        "not an amount to add to what is already set."),
}

SECTIONS = [
    ("Take-up refusals",
     "Shown across the status gutter when a pass is stopped before it starts. "
     "Every one of these begins with **Cut aborted**, because the first thing "
     "an operator needs to know is the state of the machine, not the name of "
     "the fault.",
     [("takeup.unconfirmed", ELS_TAKEUP_MESSAGES[ELS_TAKEUP_ERR_UNCONFIRMED]),
      ("takeup.wrong_way", ELS_TAKEUP_WRONG_WAY),
      ("takeup.servomode", ELS_TAKEUP_MESSAGES[ELS_CAL_ERR_SERVOMODE]),
      ("takeup.timeout", ELS_TAKEUP_MESSAGES[ELS_TAKEUP_ERR_TIMEOUT]),
      ("takeup.timeout_latched", ELS_TAKEUP_TIMEOUT_LATCHED),
      ("takeup.unknown", ELS_TAKEUP_UNKNOWN)]),
    ("Backlash calibration refusals",
     "Shown in the calibration dialog. None of these move the machine.",
     [("cal.enabled", ELS_CAL_MESSAGES[ELS_CAL_ERR_ENABLED]),
      ("cal.servomode", ELS_CAL_MESSAGES[ELS_CAL_ERR_SERVOMODE]),
      ("cal.config", ELS_CAL_MESSAGES[ELS_CAL_ERR_CONFIG]),
      ("cal.no_motion", ELS_CAL_MESSAGES[ELS_CAL_ERR_NO_MOTION]),
      ("cal.aborted", ELS_CAL_MESSAGES[ELS_CAL_ERR_ABORTED])]),
    ("Thread phase offset refusals",
     "Shown in the phase-offset dialog. Nothing is applied when one of these "
     "appears — the total on screen is still whatever the controller held "
     "before you pressed Apply.",
     [("offset.offline", REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_OFFLINE]),
      ("offset.no_job", REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_JOB]),
      ("offset.no_pitch", REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_PITCH]),
      ("offset.no_geometry", REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NO_GEOMETRY]),
      ("offset.negative", REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_NEGATIVE]),
      ("offset.at_pitch", REFUSAL_TEXT[ElsFsm.PHASE_OFFSET_AT_PITCH])]),
]

HEAD = """<!-- GENERATED by tools/gen_refusal_catalogue.py -- do not edit by hand.
     The messages come from the code that renders them; the advice under each
     one lives in that script's ADVICE table. Regenerate after changing either. -->

# When it refuses

Reflex declines to do things. Each message below is reproduced **exactly** as
the machine renders it, because this page is generated from the same tables the
app draws from — if a message changes and this page is not regenerated, the
build fails.

!!! info "A refusal is the controller working"
    None of these are faults in the software. Every one is the controller
    declining to cut something it cannot verify, and the alternative in each
    case is a ruined part or a crash. The useful response is to read what it
    asked for, not to look for a way around it.

"""

FOOT = """
## Anything else

If you meet a message that is not on this page, the UI is older than the
firmware driving it — the screens say so explicitly rather than printing a bare
code. Check that the two halves are from the same release.
"""


def render():
    parts = [HEAD]
    seen = set()
    for title, blurb, rows in SECTIONS:
        parts.append(f"## {title}\n\n{blurb}\n\n")
        for key, msg in rows:
            advice = ADVICE.get(key)
            if advice is None:
                raise SystemExit(
                    f"no ADVICE for {key!r}. A refusal with no guidance is a "
                    f"dead end for the operator: add an entry to "
                    f"tools/gen_refusal_catalogue.py before shipping it.")
            seen.add(key)
            parts.append(f"> {msg}\n\n{advice}\n\n")
    unused = sorted(set(ADVICE) - seen)
    if unused:
        raise SystemExit(
            f"ADVICE has entries for messages that no longer exist: {unused}. "
            f"Remove them rather than leaving the page describing a refusal "
            f"the machine cannot produce.")
    parts.append(FOOT)
    return "".join(parts)


def main():
    text = render()
    check = "--check" in sys.argv
    existing = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8", newline="") as f:
            existing = f.read()
    if check:
        if existing != text:
            raise SystemExit(
                "docs/guide/when-it-refuses.md is stale. A message changed in "
                "the code and the page was not regenerated. Run:\n"
                "    ./.venv/bin/python ../tools/gen_refusal_catalogue.py")
        print("when-it-refuses.md is current")
        return
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    n = sum(len(rows) for _t, _b, rows in SECTIONS)
    print(f"wrote {OUT} -- {n} refusals across {len(SECTIONS)} sections"
          f"{' (unchanged)' if existing == text else ''}")


if __name__ == "__main__":
    main()
