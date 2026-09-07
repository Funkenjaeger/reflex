"""In-app update: the release filter, the derived path, and above all THE GATE.

The gate tests are the reason this module exists. ``reflex.utils.updater``
refuses to install the UI half of a release unless the firmware it just
flashed reports the register protocol version that release's UI expects, and
"refuses" has to mean the git commands are never issued -- not that a warning
was logged next to them. So the assertions here are about which commands the
runner SAW, which is the only thing that distinguishes a refusal from a
warning.

Two mutations were run against this file and both turn it red; each is named
in the test that catches it.
"""

from pathlib import Path

import pytest

from reflex.utils import updater
from reflex.utils.updater import (
    Identity,
    ProtocolMismatch,
    Release,
    UpdateRefused,
    UpdateSession,
    parse_identity,
    parse_image_info,
    parse_protocol_version,
    resolve_checkout,
    select_releases,
    verify_firmware_half,
)

CURRENT_PROTOCOL = 9
TARGET_PROTOCOL = 10        # a release that DID move the register layout
IMAGE_REV = "abc1234"


# ---------------------------------------------------------------------------
# release list
# ---------------------------------------------------------------------------

def _payload_item(tag, *, prerelease=False, draft=False, fw=True):
    assets = []
    if fw:
        v = tag.lstrip("v")
        assets.append({"name": f"reflex-fw-{v}.bin",
                       "browser_download_url": f"https://example/{tag}/fw.bin"})
    assets.append({"name": f"reflex-{tag}-py3-none-any.whl",
                   "browser_download_url": f"https://example/{tag}/w.whl"})
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft,
            "assets": assets}


PAYLOAD = [
    _payload_item("v1.2.0-rc.1", prerelease=True),
    _payload_item("v1.1.0"),
    _payload_item("v1.0.9", draft=True),
    _payload_item("v1.0.0", fw=False),      # pre-slotted-image era
]


def test_stable_only_by_default():
    got = select_releases(PAYLOAD, allow_prerelease=False)
    assert [r.tag for r in got] == ["v1.1.0"]


def test_experimental_adds_prereleases_newest_first():
    got = select_releases(PAYLOAD, allow_prerelease=True)
    assert [r.tag for r in got] == ["v1.2.0-rc.1", "v1.1.0"]


def test_a_release_with_no_firmware_asset_is_not_offered():
    """v1.0.0 predates the slotted image format and cannot supply the fw half.

    Offering it would put a release in the dropdown whose install can only
    fail at the download step -- after the operator has committed to it.
    """
    assert "v1.0.0" not in [r.tag for r in
                            select_releases(PAYLOAD, allow_prerelease=True)]


def test_drafts_are_not_offered():
    assert "v1.0.9" not in [r.tag for r in
                            select_releases(PAYLOAD, allow_prerelease=True)]


def test_the_live_repo_is_the_monorepo_not_the_archived_ui_repo():
    """Funkenjaeger/reflex-ui is ARCHIVED; its newest final tag is v1.0.0,
    which is what elspi logged as the selected release on every boot."""
    assert updater.GITHUB_RELEASES_URL == (
        "https://api.github.com/repos/Funkenjaeger/reflex/releases")
    assert "reflex-ui" not in updater.GITHUB_RELEASES_URL


def test_limit_caps_the_dropdown():
    many = [_payload_item(f"v1.{i}.0") for i in range(20)]
    assert len(select_releases(many, allow_prerelease=False, limit=10)) == 10


# ---------------------------------------------------------------------------
# parsers -- an unreadable answer must be a refusal, never a default
# ---------------------------------------------------------------------------

IDENTITY_LINE = ("idMagic=0x454c stage=application windowVersion=1 "
                 "rev=abc1234 appProtocol=9")


def test_parse_identity_reads_the_flasher_format():
    ident = parse_identity(IDENTITY_LINE)
    assert ident.stage == "application"
    assert ident.build_rev == "abc1234"
    assert ident.app_protocol == 9


def test_parse_identity_reads_a_dirty_rev():
    ident = parse_identity(IDENTITY_LINE.replace("rev=abc1234",
                                                 "rev=abc1234-dirty"))
    assert ident.build_rev == "abc1234-dirty"


@pytest.mark.parametrize("text", ["", "no identity window at 2048: timeout",
                                  "stage=application appProtocol=9"])
def test_unreadable_identity_refuses_rather_than_defaulting(text):
    """The gate's input. A parser that returned 0 here would hand the gate a
    protocol version nobody measured, and the gate would compare it."""
    with pytest.raises(UpdateRefused):
        parse_identity(text)


def test_parse_image_info():
    info = parse_image_info(
        "/tmp/x.bin: rev abc1234, 61440 bytes (file 61440), crc32 0x11223344, header v1")
    assert info.rev == "abc1234"
    assert info.length == 61440


def test_unreadable_image_info_refuses():
    with pytest.raises(UpdateRefused):
        parse_image_info("/tmp/x.bin: INVALID: bad magic 0xffffffff")


def test_parse_protocol_version_from_source_text():
    src = "x = 1\nELS_PROTOCOL_VERSION = 12        # comment\ny = 2\n"
    assert parse_protocol_version(src) == 12


def test_target_protocol_missing_refuses():
    with pytest.raises(UpdateRefused):
        parse_protocol_version("nothing to see here")


def test_this_checkouts_own_devices_py_parses():
    """The parse must work on the real file, not only a synthetic one -- it is
    read out of a git tag whose formatting nobody controls at install time."""
    src = (Path(updater.__file__).parent / "devices.py").read_text(encoding="utf-8")
    from reflex.utils.devices import ELS_PROTOCOL_VERSION
    assert parse_protocol_version(src) == ELS_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# the install path is DERIVED
# ---------------------------------------------------------------------------

def test_resolve_checkout_finds_this_monorepo():
    root = resolve_checkout()
    assert (root / "fw" / "scripts" / "modbus-flash.py").is_file()
    assert (root / "ui" / "pyproject.toml").is_file()


def test_resolve_checkout_refuses_a_tree_that_is_not_a_checkout(tmp_path):
    """The old screen installed into the literal /reflex-ui and aborted when
    it was missing. Refusing is still right -- but it must be refused at
    preflight, by name, and not by a hardcoded path happening to be absent."""
    fake = tmp_path / "ui" / "reflex" / "utils" / "updater.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    with pytest.raises(UpdateRefused) as e:
        resolve_checkout(fake)
    assert "monorepo git checkout" in str(e.value)


def test_no_hardcoded_install_path_survives():
    src = Path(updater.__file__).read_text(encoding="utf-8")
    screen = (Path(updater.__file__).parents[1] / "components" / "screens"
              / "update_screen.py").read_text(encoding="utf-8")
    for text in (src, screen):
        assert '"/reflex-ui"' not in text
        assert '"/home/default/projects/reflex"' not in text


# ---------------------------------------------------------------------------
# THE GATE, in isolation
# ---------------------------------------------------------------------------

def _ident(protocol=TARGET_PROTOCOL, stage="application", rev=IMAGE_REV):
    return Identity(stage=stage, build_rev=rev, app_protocol=protocol)


def test_gate_passes_a_matched_pair():
    v = verify_firmware_half(_ident(), TARGET_PROTOCOL, "v1.2.0",
                             expected_rev=IMAGE_REV)
    assert v.target_protocol == TARGET_PROTOCOL
    assert v.identity.app_protocol == TARGET_PROTOCOL


def test_gate_refuses_a_protocol_mismatch():
    with pytest.raises(ProtocolMismatch) as e:
        verify_firmware_half(_ident(protocol=TARGET_PROTOCOL - 1),
                             TARGET_PROTOCOL, "v1.2.0", expected_rev=IMAGE_REV)
    assert "REFUSED" in str(e.value)
    assert "NOT installed" in str(e.value)


def test_gate_refuses_a_board_left_in_the_bootloader():
    with pytest.raises(ProtocolMismatch):
        verify_firmware_half(_ident(stage="bootloader"), TARGET_PROTOCOL,
                             "v1.2.0", expected_rev=IMAGE_REV)


def test_gate_refuses_when_the_image_did_not_take():
    """Covers the bootloader's own swap-back: the board comes up healthy,
    running the PREVIOUS image, and every other signal looks fine."""
    with pytest.raises(ProtocolMismatch):
        verify_firmware_half(_ident(rev="0000001"), TARGET_PROTOCOL, "v1.2.0",
                             expected_rev=IMAGE_REV)


# ---------------------------------------------------------------------------
# the whole sequence, against a fake runner
# ---------------------------------------------------------------------------

class FakeRunner:
    """Answers the four tools the session shells out to, and records the lot."""

    def __init__(self, *, board_protocol_after, board_protocol_before=CURRENT_PROTOCOL,
                 target_protocol=TARGET_PROTOCOL, image_rev=IMAGE_REV,
                 board_rev_after=IMAGE_REV, image_valid=True, dirty="",
                 fail=None):
        self.calls = []
        self.board_protocol_before = board_protocol_before
        self.board_protocol_after = board_protocol_after
        self.target_protocol = target_protocol
        self.image_rev = image_rev
        self.board_rev_after = board_rev_after
        self.image_valid = image_valid
        self.dirty = dirty
        self.fail = fail or set()
        self.flashed = False

    def __call__(self, argv, cwd=None, timeout=None, emit=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        joined = " ".join(argv)

        for marker in self.fail:
            if marker in joined:
                return 1, f"boom: {marker}"

        if "--identity" in argv:
            protocol = (self.board_protocol_after if self.flashed
                        else self.board_protocol_before)
            rev = self.board_rev_after if self.flashed else "0000001"
            return 0, (f"idMagic=0x454c stage=application windowVersion=1 "
                       f"rev={rev} appProtocol={protocol}")
        if "modbus-flash.py" in joined:
            self.flashed = True
            return 0, "VERDICT: OK"
        if "reflex_image.py" in joined and "info" in argv:
            if not self.image_valid:
                return 1, "x.bin: INVALID: bad magic 0xffffffff"
            return 0, (f"x.bin: rev {self.image_rev}, 61440 bytes "
                       f"(file 61440), crc32 0x1, header v1")
        if argv[:2] == ["git", "status"]:
            return 0, self.dirty
        if argv[:2] == ["git", "show"]:
            return 0, f"ELS_PROTOCOL_VERSION = {self.target_protocol}\n"
        if argv[0] == "git":
            return 0, ""
        if argv[0].endswith("uv"):
            return 0, "Resolved 1 package"
        return 0, ""

    # -- what the assertions ask -----------------------------------------
    def ran(self, *needles):
        return [c for c in self.calls
                if all(n in " ".join(c) for n in needles)]

    @property
    def touched_the_ui_half(self):
        """Any command that changes the installed UI. THE thing a refusal
        must leave at zero."""
        return (self.ran("git", "checkout") + self.ran("uv", "sync"))


RELEASE = Release(tag="v1.2.0", prerelease=False,
                  firmware_url="https://example/fw.bin",
                  firmware_name="reflex-fw-1.2.0.bin")


def _session(runner, tmp_path, **kw):
    restarts = []
    s = UpdateSession(
        checkout=tmp_path / "checkout",
        port="/dev/serial0",
        current_protocol=CURRENT_PROTOCOL,
        workdir=tmp_path / "work",
        runner=runner,
        download=lambda url, dest: (dest.write_bytes(b"x"), dest)[1],
        fetch_json=lambda url: PAYLOAD,
        uv_finder=lambda: "/usr/bin/uv",
        restart=lambda: restarts.append(1),
        python="/usr/bin/python3",
        **kw,
    )
    s.restarts = restarts
    return s


def test_happy_path_flashes_then_installs_in_that_order(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(RELEASE)

    flash_at = r.calls.index(r.ran("modbus-flash.py", "reflex-fw-1.2.0.bin")[0])
    checkout_at = r.calls.index(r.ran("git", "checkout")[0])
    assert flash_at < checkout_at, "the recoverable half goes first"
    assert r.ran("uv", "sync")
    assert s.restarts == [1]


def test_a_release_that_moves_the_protocol_is_the_normal_case(tmp_path):
    """MUTATION EVIDENCE #1. The gate compares the flashed firmware against
    the TARGET UI's ELS_PROTOCOL_VERSION, read from the tag -- not against the
    RUNNING UI's. Changing ``prepared.target_protocol`` to
    ``self.current_protocol`` in flash_firmware turns this test red, because
    the whole point of a protocol-bumping release is that the two differ.

    A gate built the obvious way would refuse exactly the updates that are
    correct and pass exactly the ones that are not.
    """
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    assert TARGET_PROTOCOL != CURRENT_PROTOCOL
    s = _session(r, tmp_path)
    s.run(RELEASE)
    assert r.touched_the_ui_half


def test_a_protocol_mismatch_refuses_and_installs_nothing(tmp_path):
    """MUTATION EVIDENCE #2, the load-bearing one. Turning the raise in
    ``verify_firmware_half`` into a log line -- warn instead of refuse -- turns
    this red on ``touched_the_ui_half``, not merely on the exception: the
    assertion is about which commands ran, so a refusal that still checked out
    the tag could not pass it.
    """
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL + 1)
    s = _session(r, tmp_path)
    with pytest.raises(ProtocolMismatch):
        s.run(RELEASE)

    assert r.ran("modbus-flash.py", "reflex-fw-1.2.0.bin"), "it did flash"
    assert r.touched_the_ui_half == [], "and then installed nothing"
    assert s.restarts == []


def test_a_mismatch_resumes_the_link_so_the_operator_can_see_why(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL + 1)
    s = _session(r, tmp_path)
    events = []
    with pytest.raises(ProtocolMismatch):
        s.run(RELEASE, pause_link=lambda: events.append("pause"),
              resume_link=lambda: events.append("resume"))
    assert events == ["pause", "resume"]


def test_install_ui_half_cannot_be_called_without_a_verdict(tmp_path):
    """The gate is a precondition of the installer, not a step beside it."""
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    prepared = s.preflight(RELEASE)
    with pytest.raises(UpdateRefused):
        s.install_ui_half(prepared, None)
    assert r.touched_the_ui_half == []


def test_install_ui_half_rejects_a_verdict_about_a_different_update(tmp_path):
    """A hand-built verdict has to be a TRUTHFUL one to get past."""
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    prepared = s.preflight(RELEASE)
    forged = updater.FirmwareVerdict(identity=_ident(protocol=CURRENT_PROTOCOL),
                                     target_protocol=CURRENT_PROTOCOL,
                                     target_tag="v1.2.0")
    with pytest.raises(ProtocolMismatch):
        s.install_ui_half(prepared, forged)
    assert r.touched_the_ui_half == []


# -- preflight refuses before anything is written ---------------------------

def test_preflight_refuses_a_dirty_checkout_without_flashing(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL, dirty=" M ui/x.py")
    s = _session(r, tmp_path)
    with pytest.raises(UpdateRefused) as e:
        s.run(RELEASE)
    assert "uncommitted" in str(e.value)
    assert r.ran("modbus-flash.py") == []


def test_preflight_refuses_a_non_slotted_firmware_asset(tmp_path):
    """TODAY'S RELEASES LAND HERE, and that is the check working. release.yml
    publishes fw/build/reflex-fw-<V>.bin from the default cmake configuration
    -- the legacy 0x08000000 layout with no RFLX image header -- so v1.1.0's
    asset is not something the bootloader would accept. Refusing at preflight
    means the operator is told before the erase, not after.
    """
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL, image_valid=False)
    s = _session(r, tmp_path)
    with pytest.raises(UpdateRefused) as e:
        s.run(RELEASE)
    assert "slotted application image" in str(e.value)
    assert r.ran("modbus-flash.py") == []


def test_preflight_refuses_when_uv_is_missing(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)

    def no_uv():
        raise UpdateRefused("uv was not found")

    s = _session(r, tmp_path)
    s._uv_finder = no_uv
    with pytest.raises(UpdateRefused):
        s.run(RELEASE)
    assert r.calls == [], "not even a git status before we know we can finish"


def test_refuses_a_machine_that_is_already_mismatched(tmp_path):
    """Starting from a mismatched pair, a failed update would be blamed on the
    update. Say so first, before the erase."""
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL,
                   board_protocol_before=CURRENT_PROTOCOL - 1)
    s = _session(r, tmp_path)
    with pytest.raises(UpdateRefused) as e:
        s.run(RELEASE)
    assert "ALREADY mismatched" in str(e.value)
    assert r.ran("modbus-flash.py", "reflex-fw-1.2.0.bin") == []


def test_a_failed_flash_does_not_install_the_ui_half(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL,
                   fail={"reflex-fw-1.2.0.bin"})
    s = _session(r, tmp_path)
    with pytest.raises(UpdateRefused):
        s.run(RELEASE)
    assert r.touched_the_ui_half == []


def test_list_releases_goes_through_the_same_filter(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    assert [x.tag for x in s.list_releases(allow_prerelease=False)] == ["v1.1.0"]
    assert [x.tag for x in s.list_releases(allow_prerelease=True)] == [
        "v1.2.0-rc.1", "v1.1.0"]
