"""release_version: the tag spelling of the installed package, and canonical
comparison of the two spellings (2026-09-25; see the module docstring)."""
import pytest

from reflex.utils import release_version as rv


@pytest.mark.parametrize("package, tag", [
    ("1.2.0rc7", "v1.2.0-rc.7"),
    ("1.2.0rc12", "v1.2.0-rc.12"),
    ("1.1.0", "v1.1.0"),
    ("1.2.0", "v1.2.0"),
    ("1.2.10rc1", "v1.2.10-rc.1"),
    ("1.3.0a2", "v1.3.0-alpha.2"),
    ("1.3.0b1", "v1.3.0-beta.1"),
    # Not a release shape: shown as is, not guessed at.
    ("1.2.0rc7.dev3+g1234", "v1.2.0rc7.dev3+g1234"),
])
def test_tag_for(package, tag):
    assert rv.tag_for(package) == tag


@pytest.mark.parametrize("a, b", [
    ("v1.2.0-rc.7", "1.2.0rc7"),
    ("v1.2.0-rc.7", "v1.2.0rc7"),
    ("v1.2.0-rc.7", "V1.2.0-RC.7"),
    ("v1.1.0", "1.1.0"),
    ("v1.3.0-alpha.2", "1.3.0a2"),
])
def test_same_release(a, b):
    assert rv.same_release(a, b)
    assert rv.same_release(b, a)


@pytest.mark.parametrize("a, b", [
    ("v1.2.0-rc.7", "v1.2.0-rc.6"),
    ("v1.2.0-rc.7", "v1.2.0"),          # a pre-release is not its final
    ("v1.2.0", "v1.2.1"),
    ("v1.2.0-rc.1", "v1.2.0-rc.10"),
    ("", "v1.2.0"),
    ("v1.2.0", ""),
])
def test_different_releases(a, b):
    assert not rv.same_release(a, b)


def test_every_tag_round_trips_through_its_package_spelling():
    """The shapes the release workflow cuts: tag -> package (as uv normalizes
    it) -> tag again."""
    for tag, package in [("v1.1.0", "1.1.0"), ("v1.1.0-rc.1", "1.1.0rc1"),
                         ("v1.2.0-rc.7", "1.2.0rc7")]:
        assert rv.tag_for(package) == tag
        assert rv.same_release(tag, package)
