"""Unit test for the release-time uv.lock parity guard
(``ui/scripts/lock_version_parity.py``).

The guard's job is to refuse a release whose ``ui/uv.lock`` does not declare the
version being stamped -- the defect measured on integration 2026-09-11, where
VERSION said 1.1.0 and the lock still said 1.1.0rc1 because the workflow ran
``uv sync`` after the stamp commit rather than ``uv lock`` before it.

WHY THIS TEST DOES NOT ASSERT ON THE LIVE TREE. The obvious test -- "the repo's
own VERSION and ui/uv.lock agree" -- would be red on this very branch, and red
for a reason that is not a bug in anyone's change: parity is a property of a
RELEASE COMMIT, produced by the stamp step, not of every commit on every branch.
A tagged commit whose lock has not yet been merged back to integration leaves
the two legitimately apart, and a test that goes red for that gets suppressed
within a week and takes the real guard with it. The release workflow asserts
parity where it means something (on the stamp commit, in
``.github/workflows/release.yml``); this file asserts that the assertion works.

Which is the half that actually rots: the canonicalization. ``1.1.0-rc.1`` (what
the workflow accepts and the tag says) and ``1.1.0rc1`` (what uv writes) are the
same version, and a guard that compared strings would be red on every
pre-release until someone "fixed" it into one that compares nothing.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lock_version_parity.py"


def _load():
    spec = importlib.util.spec_from_file_location("lock_version_parity", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load()


def test_the_script_is_where_the_workflow_calls_it():
    """.github/workflows/release.yml runs this by path. A rename that misses the
    workflow turns the guard into a job failure at best and a skipped step at
    worst."""
    assert _SCRIPT.is_file()
    workflow = _SCRIPT.parents[2] / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "ui/scripts/lock_version_parity.py" in text
    assert "lock_version_parity.py self-test" in text, \
        "the workflow must run the self-test, so every release re-proves the guard can fail"


def test_self_test_is_green():
    """The same command the release job runs, with the same fixtures."""
    assert guard.self_test() == 0


def test_the_measured_defect_is_refused():
    """VERSION 1.1.0 against a lock left at 1.1.0rc1 -- integration, 2026-09-11."""
    problems, _ = guard.check(guard._lock("1.1.0rc1"), "1.1.0")
    assert problems
    assert "1.1.0rc1" in problems[0]


def test_a_stamped_pair_is_accepted():
    problems, desc = guard.check(guard._lock("1.1.0"), "1.1.0")
    assert problems == []
    assert "1.1.0" in desc


@pytest.mark.parametrize("version_file, lock", [
    ("1.1.0-rc.1", "1.1.0rc1"),
    ("1.1.0-alpha.2", "1.1.0a2"),
    ("1.1.0-beta.1", "1.1.0b1"),
    ("1.1.0", "1.1.0"),
])
def test_uv_normalization_is_not_a_mismatch(version_file, lock):
    """The repo spells pre-releases with separators; uv writes PEP 440 normal
    form. These are the same version and the guard must say so."""
    problems, desc = guard.check(guard._lock(lock), version_file)
    assert problems == [], desc


@pytest.mark.parametrize("version_file, lock", [
    ("1.1.0", "1.1.0rc1"),      # final stamped over a pre-release lock
    ("1.1.0-rc.1", "1.1.0"),    # and the reverse
    ("1.1.0-rc.2", "1.1.0rc1"), # one rc behind
    ("1.1.0", "1.1.1"),
])
def test_real_mismatches_are_still_caught(version_file, lock):
    """Canonicalization must collapse spellings WITHOUT collapsing versions."""
    problems, _ = guard.check(guard._lock(lock), version_file)
    assert problems


@pytest.mark.parametrize("lock_text, why", [
    ("version = 1\n", "a lock with no package entries at all"),
    ("[[[not toml\n", "a lock that does not parse"),
])
def test_a_lock_it_cannot_read_is_a_failure_not_a_pass(lock_text, why):
    problems, _ = guard.check(lock_text, "1.1.0")
    assert problems, why
