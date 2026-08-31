"""The in-app update screen is unreachable in 1.1.0, and kept for a redesign.

It was broken three ways at once, and all three only surfaced when a user
pressed Install:

  * it queried github.com/repos/Funkenjaeger/reflex-ui, an ARCHIVED repo, so
    the only non-prerelease tag it could offer was v1.0.0
  * it installed into /reflex-ui, deleted at the 2026-08-25 monorepo cutover
  * a UI-only updater does not fit lockstep fw+ui releases whose firmware half
    needs an ST-Link

These assertions are on source rather than behaviour on purpose. "This feature
is not exposed" is a statement about wiring, and wiring is what a future change
would silently restore -- re-adding the button is a two-line edit that no
behavioural test would notice.
"""
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "reflex"


def _read(rel):
    return (UI / rel).read_text(encoding="utf-8")


def test_no_setup_button_navigates_to_it():
    kv = _read("components/screens/setup_screen.kv")
    assert 'goto("update")' not in kv
    assert "goto('update')" not in kv


def test_the_screen_is_not_registered():
    """The manager must not add it, so goto("update") could not work anyway."""
    src = _read("components/manager.py")
    assert not re.search(r"^\s*self\.add_widget\(UpdateScreen\(", src, re.M)
    assert not re.search(
        r"^\s*from reflex\.components\.screens\.update_screen import", src, re.M)


def test_nothing_else_routes_to_it():
    """A second entry point would be just as reachable as the button was."""
    offenders = []
    for path in UI.rglob("*"):
        if path.suffix not in (".py", ".kv") or "update_screen" in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"""goto\(\s*['"]update['"]\s*\)""", text):
            offenders.append(str(path.relative_to(UI)))
    assert offenders == []


def test_the_screen_is_kept_not_deleted():
    """Unwired, deliberately -- not removed.

    The redesign question (what an in-app update means when a release is an
    fw+ui pair) is tracked separately, and this code is where answering it
    starts. Deleting it to make the tests above pass would throw that away.
    """
    assert (UI / "components/screens/update_screen.py").is_file()
    assert (UI / "components/screens/update_screen.kv").is_file()


@pytest.mark.parametrize("stale,why", [
    ("Funkenjaeger/reflex-ui", "the archived pre-weld repo"),
    ('"/reflex-ui"', "the checkout deleted at the monorepo cutover"),
])
def test_the_known_staleness_is_still_recorded(stale, why):
    """If someone revives this screen, these are the two things to fix first.

    Kept as an assertion rather than a comment so that a revival which drops
    them has to confront them: the test names what is wrong, and it lives next
    to the reason the screen was unwired.
    """
    assert stale in _read("components/screens/update_screen.py"), why
