"""The in-app update screen is REACHABLE again, and these pin what changed.

This file is ``test_update_screen_unwired.py`` renamed, not a new one. That
file existed to hold a DECISION -- "a UI-only updater does not fit lockstep
fw+ui releases" -- and it asserted the screen's source still existed precisely
so that nobody could satisfy it by deleting the feature instead of deciding.
The decision has now been made and executed (full fw+ui, 2026-09-07, once
``fw/scripts/modbus-flash.py`` made the firmware half flashable over the
existing RS-485 link), so the file changes with it rather than disappearing.

Its three old assertions are inverted here: the Setup button navigates, the
manager registers, and the two named staleness faults -- the archived
``Funkenjaeger/reflex-ui`` endpoint and the literal ``/reflex-ui`` install path
-- are GONE rather than merely recorded.

The fourth group is new, and it is the one worth keeping. These are STRUCTURAL
assertions about the protocol gate: that nothing reaches the git commands
except through :func:`reflex.utils.updater.verify_firmware_half`, and that no
confirmation dialog sits over it. Behaviour tests for the gate itself are in
``tests/utils/test_updater.py``; what cannot be tested behaviourally is a
SECOND route added later, which is exactly how "refuse" quietly becomes "warn".
"""
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "reflex"


def _read(rel):
    return (UI / rel).read_text(encoding="utf-8")


# ── it is wired ────────────────────────────────────────────────────────

def test_the_setup_button_navigates_to_it():
    kv = _read("components/screens/setup_screen.kv")
    assert 'goto("update")' in kv


def test_the_screen_is_registered():
    src = _read("components/manager.py")
    assert re.search(r"^\s*self\.add_widget\(UpdateScreen\(name=\"update\"\)\)",
                     src, re.M)
    assert re.search(
        r"^\s*from reflex\.components\.screens\.update_screen import UpdateScreen",
        src, re.M)


def test_the_screen_files_are_still_there():
    assert (UI / "components/screens/update_screen.py").is_file()
    assert (UI / "components/screens/update_screen.kv").is_file()
    assert (UI / "utils/updater.py").is_file()


# ── the two faults it was unwired for are fixed ────────────────────────

@pytest.mark.parametrize("stale,why", [
    ("repos/Funkenjaeger/reflex-ui", "the archived pre-weld repo"),
    ('"/reflex-ui"', "the checkout deleted at the monorepo cutover"),
    ("'/reflex-ui'", "the checkout deleted at the monorepo cutover"),
])
def test_the_known_staleness_is_gone(stale, why):
    """These were assertions that the faults were still RECORDED, back when
    the screen was unreachable and fixing them was premature. Now that a user
    can press Install, they are assertions that the faults are absent.

    MATCHED ON THE CODE FORM, not on the bare name. Both modules discuss the
    archived repo and the deleted path at length in their docstrings -- that
    prose is the record of why the feature was pulled and is worth keeping. A
    bare-substring assertion would fire on the explanation of the fix, so what
    is asserted is the URL PATH and the quoted string LITERAL: the forms that
    could actually be used.
    """
    for rel in ("components/screens/update_screen.py", "utils/updater.py"):
        assert stale not in _read(rel), f"{rel} still names {why}"


def test_it_queries_the_monorepo():
    assert "Funkenjaeger/reflex/releases" in _read("utils/updater.py")


# ── the gate has no second door ────────────────────────────────────────

def _updater_src():
    return _read("utils/updater.py")


def test_the_ui_half_is_installed_from_exactly_one_place():
    """git checkout and uv sync appear only inside install_ui_half.

    A behaviour test can prove the gate refuses. It cannot prove that a later
    change did not add a second path to the git commands that skips it -- and
    that is the shape this whole feature was pulled for once already.
    """
    src = _updater_src()
    body = src[src.index("def install_ui_half"):src.index("def restart_service")]
    for needle in ('"checkout"', '"sync"'):
        assert needle in body
        assert src.count(needle) == body.count(needle), (
            f"{needle} appears outside install_ui_half")


def test_install_ui_half_takes_a_verdict():
    assert re.search(r"def install_ui_half\(self, prepared[^)]*verdict",
                     _updater_src())


def test_only_verify_firmware_half_builds_a_verdict():
    """FirmwareVerdict(...) is constructed in exactly one place."""
    src = _updater_src()
    constructions = re.findall(r"(?<!class )FirmwareVerdict\(", src)
    assert len(constructions) == 1, (
        "a second FirmwareVerdict construction is a second way past the gate")


def test_no_confirmation_dialog_sits_over_the_protocol_check():
    """The screen has exactly one Popup, and it is the pre-release warning.

    An "Install Anyway" on a mismatched pair would defeat the entire feature:
    the reason in-app update was allowed back is that it CANNOT leave the two
    halves disagreeing, and a dialog is a way to make it able to.
    """
    screen = _read("components/screens/update_screen.py")
    assert screen.count("Popup(") == 1

    # The one dialog is reached only from the pre-release branch, and nothing
    # in the gate's module can build one. Asserted on the code form: the
    # screen's docstrings talk about the protocol check at length, so a search
    # for the word would fire on the sentence explaining that there is no
    # dialog for it.
    assert re.search(r"if release\.prerelease:\s+self\._confirm_prerelease",
                     screen)
    assert "Popup(" not in _updater_src()
    # The CALL form. The screen's docstring cross-references the gate by name,
    # which is the reference a reader wants and not a call site.
    assert "verify_firmware_half(" not in screen, (
        "the gate is called from the session, not from the screen, so no UI "
        "code sits between the check and its consequence")


def test_the_gate_raises_rather_than_logging():
    """`refuse, do not warn`, asserted on the code form.

    Matching a bare word like "refuse" would be satisfied by the comment
    explaining the refusal, so this matches the raise statements themselves.
    """
    src = _updater_src()
    gate = src[src.index("def verify_firmware_half"):
               src.index("# ---", src.index("def verify_firmware_half"))]
    assert gate.count("raise ProtocolMismatch(") == 3
    assert "log.warning" not in gate
    assert "log.error" not in gate
