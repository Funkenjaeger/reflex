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


@pytest.mark.parametrize("tag, shown", [
    ("v1.3.0-alpha.12", "v1.3.0-a.12"),
    ("v1.3.0-beta.2", "v1.3.0-b.2"),
    ("v1.2.0-rc.7", "v1.2.0-rc.7"),
    ("v1.2.0", "v1.2.0"),
    ("v1.2.0rc7.dev3+g1234", "v1.2.0rc7.dev3+g1234"),
])
def test_status_label(tag, shown):
    assert rv.status_label(tag) == shown


@pytest.mark.parametrize("numbers", ["1.2.0", "1.2.10", "1.12.10"])
@pytest.mark.parametrize("n", [1, 7, 12, 100])
def test_status_label_is_never_wider_than_the_rc(numbers, n):
    """The status bar's box shrinks its font to fit (92 dp), so an alpha
    shown in full would render smaller than any rc. Shortened, it cannot."""
    for word in ("alpha", "beta"):
        shown = rv.status_label(rv.tag_for(f"{numbers}{word[0]}{n}"))
        assert len(shown) <= len(f"v{numbers}-rc.{n}"), shown
        assert rv.same_release(shown, f"{numbers}{word[0]}{n}")


def test_the_status_bar_shows_the_status_label():
    """The kv binding, checked in its code form: the suite has no GL context
    to render the label (see tests/components/test_auto_size_label.py)."""
    from pathlib import Path
    import reflex
    kv = (Path(reflex.__file__).parent / "components/home/statusbar.kv").read_text()
    assert "text: release_version.status_label(app.version)" in kv
    assert "text: app.version\n" not in kv


# ── Integration builds: dev releases of the next rc (2026-10-03) ─────────

@pytest.mark.parametrize("package, tag", [
    ("1.2.2rc2.dev1", "v1.2.2-rc.2.dev.1"),
    ("1.2.2rc12.dev30", "v1.2.2-rc.12.dev.30"),
    ("1.3.0.dev2", "v1.3.0-dev.2"),
])
def test_tag_for_a_dev_build(package, tag):
    assert rv.tag_for(package) == tag
    assert rv.same_release(tag, package)


def test_dev_builds_are_not_their_rc():
    assert not rv.same_release("v1.2.2-rc.2.dev.1", "v1.2.2-rc.2")
    assert not rv.same_release("v1.2.2-rc.2.dev.1", "v1.2.2-rc.2.dev.2")


def test_dev_builds_sort_between_the_rcs():
    """The whole point of the scheme: builds and rcs alternate on one
    version, and every dev build sorts just below the rc it leads to."""
    ordered = ["v1.2.1", "v1.2.2-alpha.1", "v1.2.2-alpha.2", "v1.2.2-rc.1",
               "v1.2.2-rc.2.dev.1", "v1.2.2-rc.2.dev.2", "v1.2.2-rc.2",
               "v1.2.2-rc.3.dev.1", "v1.2.2", "v1.2.3-dev.1",
               "v1.2.3-rc.1.dev.1", "v1.2.3-rc.1"]
    assert sorted(reversed(ordered), key=rv.precedence_key) == ordered


def test_package_and_tag_spellings_sort_alike():
    assert rv.precedence_key("1.2.2rc2.dev1") == rv.precedence_key("v1.2.2-rc.2.dev.1")


@pytest.mark.parametrize("full, shown", [
    ("v1.2.2-rc.2.dev.1", "v1.2.2-rc2.d1"),
    ("v1.12.10-rc.12.dev.30", "v1.12.10-rc12.d30"),
])
def test_status_label_for_a_dev_build(full, shown):
    """Both numbers stay (rc2.d1 and rc3.d1 are different builds); it costs
    one character plus the dev number's digits over the rc it leads to."""
    assert rv.status_label(full) == shown
    assert rv.same_release(shown, full)
    rc, dev = full.split(".dev.")
    assert len(shown) == len(rc) + 1 + len(dev)
