"""The updater with an integration build source configured.

Two things are proved here. First, a PUBLIC release takes byte-for-byte the
path it always took, even on a machine that has a home source: the same
GitHub fetch, no environment handed to the runner, no credential on the
download. Second, a HOME release fetches exactly its own tag from the home
source with the credential in the environment only, downloads with it only
from the source's own origin, and then goes through the same gate as any
other release. The assertions are about what the runner and the downloader
SAW, as in test_updater.py."""
import base64

import pytest

from reflex.utils import updater
from reflex.utils.release_source import parse_home_source
from reflex.utils.updater import Release, UpdateRefused, UpdateSession
from tests.utils.test_updater import (CURRENT_PROTOCOL, PAYLOAD, TARGET_PROTOCOL,
                                      FakeRunner, _payload_item)
from reflex.utils.image_requirement import MINIMUM_IMAGE_RELEASE

TOKEN = "fedcba9876543210fedcba9876543210fedcba98"
HOME = parse_home_source(
    "RELEASES_URL=https://forge.example/api/v1/repos/acme/reflex/releases\n"
    "GIT_URL=https://forge.example/acme/reflex.git\n"
    f"USER=bench\nTOKEN={TOKEN}\n")
BASIC = "Basic " + base64.b64encode(f"bench:{TOKEN}".encode()).decode()


def _home_item(tag):
    v = tag.lstrip("v")
    return {"tag_name": tag, "prerelease": True, "draft": False, "assets": [
        {"name": f"reflex-app-{v}.bin",
         "browser_download_url": f"https://forge.example/acme/reflex/releases/download/{tag}/reflex-app-{v}.bin"}]}


HOME_PAYLOAD = [_home_item("v1.2.0-alpha.3"), _home_item("v1.1.0-alpha.9")]
ALPHA = Release(tag="v1.2.0-alpha.3", prerelease=True,
                firmware_url="https://forge.example/acme/reflex/releases/download/v1.2.0-alpha.3/reflex-app-1.2.0-alpha.3.bin",
                firmware_name="reflex-app-1.2.0-alpha.3.bin", source="home")
PUBLIC = Release(tag="v1.2.0", prerelease=False, firmware_url="https://example/fw.bin",
                 firmware_name="reflex-app-1.2.0.bin")


class EnvRunner(FakeRunner):
    """FakeRunner that also accepts, and records, the ``env`` keyword."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.envs = []

    def __call__(self, argv, cwd=None, timeout=None, emit=None, env=None):
        self.envs.append((list(map(str, argv)), env))
        return super().__call__(argv, cwd=cwd, timeout=timeout, emit=emit)


def _session(runner, tmp_path, *, home=HOME, home_payload=None, home_error=None):
    downloads, fetches, emitted, snaps = [], [], [], []

    def fetch_json(url, headers=None):
        fetches.append((url, headers))
        if url == HOME.releases_url:
            if home_error:
                raise home_error
            return HOME_PAYLOAD if home_payload is None else home_payload
        return PAYLOAD

    def download(url, dest, headers=None):
        downloads.append((url, headers))
        dest.write_bytes(b"x")
        return dest

    release_file = tmp_path / "etc-elspi-release"
    release_file.write_text(f"ELSPI_IMAGE_RELEASE={MINIMUM_IMAGE_RELEASE}\n")
    s = UpdateSession(
        checkout=tmp_path / "checkout", port="/dev/serial0",
        current_protocol=CURRENT_PROTOCOL, workdir=tmp_path / "work",
        runner=runner, download=download, fetch_json=fetch_json,
        emit=emitted.append, uv_finder=lambda: "/usr/bin/uv",
        python="/usr/bin/python3", restart=lambda: None,
        manifest=tmp_path / "home" / "firmware" / "flashed.json",
        elspi_release_path=release_file, home_source=home,
        snapshot=lambda reason: (snaps.append(reason), tmp_path / "s.yaml")[1],
    )
    s.downloads, s.fetches, s.emitted, s.snaps = downloads, fetches, emitted, snaps
    return s


# -- the catalogue ----------------------------------------------------------

def test_without_a_home_source_the_catalogue_is_unchanged(tmp_path):
    with_none = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path, home=None)
    tags = [r.tag for r in with_none.list_release_catalogue()]
    assert tags == ["v1.2.0-rc.1", "v1.1.0"]
    assert [u for u, _ in with_none.fetches] == [updater.GITHUB_RELEASES_URL]
    assert all(r.source == "public" for r in with_none.list_release_catalogue())


def test_home_releases_merge_in_version_order(tmp_path):
    s = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path)
    cat = s.list_release_catalogue()
    # A final outranks its own alphas; an alpha of the NEXT version outranks both.
    assert [r.tag for r in cat] == ["v1.2.0-rc.1", "v1.2.0-alpha.3", "v1.1.0", "v1.1.0-alpha.9"]
    assert {r.tag: r.source for r in cat}["v1.2.0-alpha.3"] == "home"
    assert {r.tag: r.source for r in cat}["v1.1.0"] == "public"


def test_only_the_home_listing_carries_the_credential(tmp_path):
    s = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path)
    s.list_release_catalogue()
    by_url = dict(s.fetches)
    assert by_url[HOME.releases_url] == {"Authorization": BASIC}
    assert not by_url[updater.GITHUB_RELEASES_URL]


def test_a_tag_on_both_keeps_the_public_entry(tmp_path):
    s = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path,
                 home_payload=[_home_item("v1.1.0")])
    cat = s.list_release_catalogue()
    assert [r.source for r in cat if r.tag == "v1.1.0"] == ["public"]


def test_a_failing_home_source_costs_only_its_own_entries(tmp_path):
    s = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path,
                 home_error=OSError("no route to host"))
    assert [r.tag for r in s.list_release_catalogue()] == ["v1.2.0-rc.1", "v1.1.0"]
    assert any("Integration builds could not be listed" in line for line in s.emitted)


# -- installing ------------------------------------------------------------

def test_a_public_release_takes_the_unchanged_path_on_a_home_machine(tmp_path):
    """MUTATION EVIDENCE: routing every fetch through the home source, or
    passing an env on public commands, turns this red."""
    r = EnvRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(PUBLIC)
    fetches = r.ran("git", "fetch")
    assert fetches == [["git", "fetch", "--tags", "--force", updater.GITHUB_FETCH_URL]]
    assert all(env is None for _, env in r.envs), "a public update handed the runner an env"
    assert s.downloads == [(PUBLIC.firmware_url, None)]


def test_a_home_release_fetches_only_its_tag_with_the_credential_in_env(tmp_path):
    r = EnvRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(ALPHA)
    ref = "refs/tags/v1.2.0-alpha.3"
    assert r.ran("git", "fetch") == [["git", "fetch", "--no-tags", HOME.git_url, f"+{ref}:{ref}"]]
    assert not r.ran(updater.GITHUB_FETCH_URL), "the alpha was looked for on GitHub"
    (argv, env), = [(a, e) for a, e in r.envs if a[:2] == ["git", "fetch"]]
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: {BASIC}"
    for argv, _ in r.envs:
        assert TOKEN not in " ".join(argv), f"the token reached a command line: {argv}"
    # and it went through the same gate and install as any release
    assert r.ran("git", "checkout", "--detach", "v1.2.0-alpha.3")


def test_a_home_download_carries_the_credential_only_to_its_origin(tmp_path):
    s = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path)
    s.run(ALPHA)
    assert s.downloads == [(ALPHA.firmware_url, {"Authorization": BASIC})]

    foreign = Release(tag=ALPHA.tag, prerelease=True, source="home",
                      firmware_url="https://cdn.elsewhere.example/app.bin",
                      firmware_name=ALPHA.firmware_name)
    (tmp_path / "2").mkdir()
    s2 = _session(EnvRunner(board_protocol_after=TARGET_PROTOCOL), tmp_path / "2")
    s2.run(foreign)
    assert s2.downloads == [(foreign.firmware_url, {})]


def test_a_home_release_without_a_home_source_is_refused_untouched(tmp_path):
    r = EnvRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path, home=None)
    with pytest.raises(UpdateRefused, match="no integration build source"):
        s.run(ALPHA)
    assert not r.ran("modbus-flash.py") and not r.touched_the_ui_half
    assert not s.downloads


# -- the settings snapshot -------------------------------------------------

def test_settings_are_snapshotted_after_preflight_and_before_the_flash(tmp_path):
    order = []
    r = EnvRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    real_flash = s.flash_firmware
    s._snapshot = lambda reason: (order.append(("snapshot", reason)), tmp_path / "s.yaml")[1]
    s.flash_firmware = lambda p: (order.append(("flash", None)), real_flash(p))[1]
    s.run(PUBLIC)
    assert order[:2] == [("snapshot", "pre-update-v1.2.0"), ("flash", None)]


def test_a_refused_preflight_takes_no_snapshot(tmp_path):
    r = EnvRunner(board_protocol_after=TARGET_PROTOCOL, dirty=" M ui/x.py")
    s = _session(r, tmp_path)
    with pytest.raises(UpdateRefused):
        s.run(PUBLIC)
    assert s.snaps == []


def test_a_failed_snapshot_does_not_stop_the_update(tmp_path):
    r = EnvRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)

    def boom(reason):
        raise OSError("read-only file system")
    s._snapshot = boom
    s.run(PUBLIC)
    assert r.ran("git", "checkout", "--detach", "v1.2.0")
    assert any("could not be written" in line for line in s.emitted)
