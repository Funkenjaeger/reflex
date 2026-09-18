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
from reflex.utils.image_requirement import MINIMUM_IMAGE_RELEASE
from reflex.utils.updater import (
    Identity,
    ImageRelease,
    ImageTooOld,
    FirmwareProtocolMismatch,
    ProtocolMismatch,
    Release,
    RolledBack,
    UpdateRefused,
    UpdateSession,
    check_image_release,
    parse_elspi_release,
    parse_identity,
    parse_image_info,
    parse_protocol_version,
    read_image_release,
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

def _payload_item(tag, *, prerelease=False, draft=False, app=True, legacy=True):
    """One GitHub release. Since 2026-09-07 a real one carries BOTH firmware
    binaries -- the slotted ``reflex-app-*.bin`` the bootloader flashes and the
    legacy ``reflex-fw-*.bin`` for SWD recovery -- so the fixture does too. A
    payload carrying only the installable asset could not catch the selector
    reaching for the wrong one, because there would be no wrong one present.

    The slotted asset is listed FIRST, which is the unhelpful order: a selector
    that just takes the first ``.bin`` it sees would pass. ``legacy`` first
    would let that bug through.
    """
    v = tag.lstrip("v")
    assets = []
    if app:
        assets.append({"name": f"reflex-app-{v}.bin",
                       "browser_download_url": f"https://example/{tag}/app.bin"})
    if legacy:
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
    # Pre-slotted-image era: a legacy SWD binary and nothing the bootloader
    # would take. Every release before 2026-09-07 looks exactly like this.
    _payload_item("v1.0.0", app=False),
]


def test_stable_only_by_default():
    got = select_releases(PAYLOAD, allow_prerelease=False)
    assert [r.tag for r in got] == ["v1.1.0"]


def test_experimental_adds_prereleases_newest_first():
    got = select_releases(PAYLOAD, allow_prerelease=True)
    assert [r.tag for r in got] == ["v1.2.0-rc.1", "v1.1.0"]


def test_a_release_with_no_slotted_asset_is_not_offered():
    """v1.0.0 predates the slotted image format and cannot supply the fw half.

    Offering it would put a release in the dropdown whose install can only
    fail at the download step -- after the operator has committed to it.
    """
    assert "v1.0.0" not in [r.tag for r in
                            select_releases(PAYLOAD, allow_prerelease=True)]


def test_the_selected_asset_is_the_slotted_image():
    """The one the bootloader can flash: RUN slot 0x08020000, RFLX header."""
    for r in select_releases(PAYLOAD, allow_prerelease=True):
        assert r.firmware_name == f"reflex-app-{r.version}.bin"


def test_the_legacy_swd_image_is_never_selected():
    """SEEN RED against the pre-2026-09-07 selector, which matched
    ``^reflex-fw-.+\\.bin$``. Two independent failures, and both are the same
    mistake:

    * v1.1.0 publishes both binaries, and the legacy one is the one that
      selector took -- handing modbus-flash.py a 0x08000000 monolith with no
      image header. Preflight refuses it, so the machine is safe, but the
      operator is told a current release "is not a slotted application image"
      and has no way to act on that;
    * v1.0.0 publishes ONLY the legacy binary, so that selector offered it as
      installable when nothing in it can be installed.

    Neither is caught by asserting on tags alone -- v1.1.0 is a correct tag
    with the wrong asset behind it -- which is why this asserts the NAME.
    """
    picked = select_releases(PAYLOAD, allow_prerelease=True)
    assert [r.tag for r in picked] == ["v1.2.0-rc.1", "v1.1.0"]
    for r in picked:
        assert not r.firmware_name.startswith("reflex-fw-"), (
            f"{r.tag}: selected the legacy SWD image {r.firmware_name}")
        assert "/fw.bin" not in r.firmware_url, (
            f"{r.tag}: url points at the legacy asset")

    # A release that publishes only the legacy binary is not an update.
    legacy_only = [_payload_item("v0.9.0", app=False)]
    assert select_releases(legacy_only, allow_prerelease=True) == []


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
    src = "x = 1\nPROTOCOL_VERSION = 12        # comment\ny = 2\n"
    assert parse_protocol_version(src) == 12


def test_target_protocol_missing_refuses():
    with pytest.raises(UpdateRefused):
        parse_protocol_version("nothing to see here")


@pytest.mark.parametrize("src", [
    # devices.py's alias: the exact shape that made this scan match nothing
    # after the 2026-09-10 refactor, which refused every update.
    "ELS_PROTOCOL_VERSION = els_stop_map.PROTOCOL_VERSION\n",
    # the same name, but no longer a literal
    "PROTOCOL_VERSION = some.other.NAME\n",
    "PROTOCOL_VERSION = _BASE + 1\n",
    # a literal with something stuck to it is NOT a partial match
    "PROTOCOL_VERSION = 10beta\n",
])
def test_target_protocol_non_literal_refuses(src):
    """A non-literal must REFUSE, never half-match. Reading `10` out of
    `10beta`, or the alias's name as if it were a number, would put a wrong
    expectation into the firmware gate -- worse than refusing."""
    with pytest.raises(UpdateRefused):
        parse_protocol_version(src)


def test_this_checkouts_own_els_stop_map_parses():
    """The parse must work on the real file, not only a synthetic one -- it is
    read out of a git tag whose formatting nobody controls at install time.

    els_stop_map.py is GENERATED, which is why the scan points at it; this is
    what notices if the generator ever stops emitting a bare literal."""
    src = (Path(updater.__file__).parent / "els_stop_map.py").read_text(encoding="utf-8")
    from reflex.utils.els_stop_map import PROTOCOL_VERSION
    assert parse_protocol_version(src) == PROTOCOL_VERSION
    # and it is still the number the gate is supposed to compare against
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


def test_only_the_protocol_refusal_is_the_revertible_kind():
    """The rollback keys on the exception TYPE. A board left in the
    bootloader has no application to pair, and a foreign rev means the
    bootloader's swap-back already reverted -- reverting again would be
    refused by the bootloader, or worse, undo a swap-back that saved it."""
    with pytest.raises(FirmwareProtocolMismatch):
        verify_firmware_half(_ident(protocol=TARGET_PROTOCOL - 1),
                             TARGET_PROTOCOL, "v1.2.0", expected_rev=IMAGE_REV)
    for ident in (_ident(stage="bootloader"), _ident(rev="0000001")):
        with pytest.raises(ProtocolMismatch) as e:
            verify_firmware_half(ident, TARGET_PROTOCOL, "v1.2.0",
                                 expected_rev=IMAGE_REV)
        assert not isinstance(e.value, FirmwareProtocolMismatch), ident


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
                 fail=None, board_after_revert=None):
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
        self.reverted = False
        # (rev, protocol) the board reports after a revert; default: exactly
        # what it ran before the update, i.e. the revert worked
        self.board_after_revert = board_after_revert or ("0000001", board_protocol_before)

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
            if self.reverted:
                rev, protocol = self.board_after_revert
            return 0, (f"idMagic=0x454c stage=application windowVersion=1 "
                       f"rev={rev} appProtocol={protocol}")
        if "modbus-flash.py" in joined and "--revert" in argv:
            self.reverted = True
            return 0, "VERDICT: OK -- reverted"
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
            # what `git show <tag>:ui/reflex/utils/els_stop_map.py` yields
            return 0, f"PROTOCOL_VERSION = {self.target_protocol}\n"
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
                  firmware_name="reflex-app-1.2.0.bin")


def _session(runner, tmp_path, **kw):
    restarts = []
    kw.setdefault("manifest", tmp_path / "home" / "firmware" / "flashed.json")
    kw.setdefault("checkout", tmp_path / "checkout")
    if "elspi_release_path" not in kw:
        # Every pre-existing session test predates the image-release gate
        # (order 2026-09-14#6) and has no opinion about it, so give it a
        # file that declares exactly MINIMUM_IMAGE_RELEASE -- allowed, by
        # the gate's own arithmetic (release < minimum refuses; equal does
        # not). Tests that DO want to exercise the gate pass their own
        # elspi_release_path and override this.
        release_file = tmp_path / "etc-elspi-release"
        release_file.write_text(f"ELSPI_IMAGE_RELEASE={MINIMUM_IMAGE_RELEASE}\n")
        kw["elspi_release_path"] = release_file
    s = UpdateSession(
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


def test_the_fetch_never_uses_the_checkouts_own_remote(tmp_path):
    """MUTATION EVIDENCE. Restoring ``origin`` in the fetch turns this red.

    Found on the machine 2026-09-07, on the updater's first real run: elspi's
    origin is `git@github.com-reflex:...`, an SSH alias in the *default* user's
    ~/.ssh/config, and reflex-ui runs as ROOT -- so `git fetch origin` died with
    "Could not resolve hostname github.com-reflex" before anything was flashed.

    The deeper reason this is pinned rather than left to review: a user of the
    lathe is not a developer and will never hold a GitHub SSH key, so an updater
    that fetches over SSH can only work on a machine somebody provisioned by
    hand. `origin` is whatever the clone happened to use; the updater must not
    inherit it.
    """
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(RELEASE)

    fetches = r.ran("git", "fetch")
    assert fetches, "no git fetch ran at all"
    for argv in fetches:
        assert "origin" not in argv, (
            f"the fetch used the checkout's own remote: {argv}. On elspi that "
            f"is an SSH alias root cannot resolve, and on a user's machine it "
            f"needs a key they do not have.")
        assert any(a.startswith("https://") for a in argv), (
            f"the fetch must name an explicit HTTPS URL: {argv}")


def test_happy_path_flashes_then_installs_in_that_order(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(RELEASE)

    flash_at = r.calls.index(r.ran("modbus-flash.py", "reflex-app-1.2.0.bin")[0])
    checkout_at = r.calls.index(r.ran("git", "checkout")[0])
    assert flash_at < checkout_at, "the recoverable half goes first"
    assert r.ran("uv", "sync")
    assert s.restarts == [1]


def test_the_flash_is_recorded_in_the_login_users_manifest(tmp_path):
    """The flash command carries the manifest path and says what it is
    flashing. Without ``--manifest``, modbus-flash.py's default ``~`` is ROOT's
    home here, and ot-state -- which reads /home/default/firmware -- would go
    on reporting UNKNOWN while every test stayed green."""
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(RELEASE)
    [flash] = r.ran("modbus-flash.py", "reflex-app-1.2.0.bin")
    manifest = str(tmp_path / "home" / "firmware" / "flashed.json")
    assert flash[flash.index("--manifest") + 1] == manifest
    assert flash[flash.index("--record-variant") + 1] == "release"
    assert flash[flash.index("--record-tag") + 1] == RELEASE.tag


def test_manifest_path_is_the_checkout_owners_home_not_the_process(tmp_path, monkeypatch):
    """MUTATION EVIDENCE. The UI runs as root; the manifest is the login
    user's. Deriving the path from ``Path.home()`` / ``~`` instead of the
    checkout's owner turns this red: HOME here is /root, as it is under
    reflex-ui.service, and the owner resolves to /home/default."""
    import pwd
    checkout = tmp_path / "projects" / "reflex"
    checkout.mkdir(parents=True)
    owner = checkout.stat().st_uid
    real = pwd.getpwuid

    def getpwuid(uid):
        if uid == owner:
            return type("pw", (), {"pw_dir": "/home/default"})()
        return real(uid)

    monkeypatch.setattr(pwd, "getpwuid", getpwuid)
    monkeypatch.setenv("HOME", "/root")
    assert updater.manifest_path_for(checkout) == Path("/home/default/firmware/flashed.json")

    # And a session given no explicit manifest uses exactly that.
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path, checkout=checkout, manifest=None)
    s.run(RELEASE)
    [flash] = r.ran("modbus-flash.py", "reflex-app-1.2.0.bin")
    assert flash[flash.index("--manifest") + 1] == "/home/default/firmware/flashed.json"


def test_preflight_reads_the_generated_map_from_the_tag(tmp_path):
    """WHICH FILE is asked of the tag, pinned. The FakeRunner answers any
    ``git show`` alike, so nothing else here would notice the path drifting
    back to devices.py -- where the value is an alias and the scan matches
    nothing, refusing every update.
    """
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path)
    s.run(RELEASE)
    assert r.ran("git", "show", f"{RELEASE.tag}:ui/reflex/utils/els_stop_map.py")
    assert not r.ran("git", "show", "devices.py")


def test_a_release_that_moves_the_protocol_is_the_normal_case(tmp_path):
    """MUTATION EVIDENCE #1. The gate compares the flashed firmware against
    the TARGET UI's protocol version, read from the tag's generated register
    map -- not against the RUNNING UI's. Changing ``prepared.target_protocol``
    to ``self.current_protocol`` in flash_firmware turns this test red, because
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

    assert r.ran("modbus-flash.py", "reflex-app-1.2.0.bin"), "it did flash"
    assert r.touched_the_ui_half == [], "and then installed nothing"
    assert s.restarts == []


def test_a_protocol_mismatch_rolls_the_firmware_back(tmp_path):
    """THE ROLLBACK. The refused firmware is reverted -- after the flash, to
    the rev the board ran before, recorded in the login user's manifest --
    and the refusal says the machine is as it was, because the board was
    READ back and said so."""
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL + 1)
    s = _session(r, tmp_path)
    with pytest.raises(RolledBack) as e:
        s.run(RELEASE)

    flash = r.ran("modbus-flash.py", "reflex-app-1.2.0.bin")
    revert = r.ran("modbus-flash.py", "--revert")
    assert flash and len(revert) == 1, "flashed, then reverted exactly once"
    assert r.calls.index(flash[0]) < r.calls.index(revert[0])
    argv = revert[0]
    assert argv[argv.index("--expect-rev") + 1] == "0000001", "back to BEFORE's rev"
    assert argv[argv.index("--manifest") + 1] == str(tmp_path / "home" / "firmware" / "flashed.json")
    last_identity = max(i for i, c in enumerate(r.calls) if "--identity" in c)
    assert last_identity > r.calls.index(revert[0]), \
        "the success claim rests on an identity read AFTER the revert"
    assert "restored" in str(e.value) and "0000001" in str(e.value)
    assert r.touched_the_ui_half == [] and s.restarts == []


def test_a_failed_rollback_keeps_the_mismatch_message(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL + 1, fail={"--revert"})
    s = _session(r, tmp_path)
    with pytest.raises(ProtocolMismatch) as e:
        s.run(RELEASE)
    assert not isinstance(e.value, RolledBack)
    msg = str(e.value)
    assert "under the previous UI" in msg, "the refusal's own account of the state survives"
    assert "FAILED" in msg
    assert r.touched_the_ui_half == []


def test_a_rollback_is_not_believed_on_its_exit_status(tmp_path):
    """MUTATION EVIDENCE: deleting the post-revert identity check in
    ``_roll_back`` turns this red. The revert exits 0 but the board comes
    back on some other rev; claiming 'restored' would be the lie that sends
    the operator to run a mismatched machine."""
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL + 1,
                   board_after_revert=("deadbee", CURRENT_PROTOCOL))
    s = _session(r, tmp_path)
    with pytest.raises(ProtocolMismatch) as e:
        s.run(RELEASE)
    assert not isinstance(e.value, RolledBack)
    assert "mismatched" in str(e.value)


def test_no_rollback_when_the_bootloader_already_reverted(tmp_path):
    """The board came back on a rev that is not the image's: the swap-back
    already restored the previous firmware. A second revert has nothing to
    do, and must not be attempted."""
    r = FakeRunner(board_protocol_after=CURRENT_PROTOCOL, board_rev_after="0000001")
    s = _session(r, tmp_path)
    with pytest.raises(ProtocolMismatch):
        s.run(RELEASE)
    assert r.ran("--revert") == []


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
    """Every release up to 2026-09-07 landed here, because release.yml built
    only the default cmake configuration -- the legacy 0x08000000 layout, no
    RFLX header -- and published it as the sole firmware asset. release.yml now
    builds and publishes reflex-app-<V>.bin from build-slot/ as well, and the
    selector takes that one, so the normal path no longer reaches this refusal.

    It stays because the NAME is not the evidence. reflex_image.py reading the
    bytes is: a truncated download, a re-uploaded asset, a release assembled by
    hand, or a future workflow whose post-build header patch silently stopped
    running all produce a correctly-named file the bootloader would reject.
    Refusing at preflight means the operator is told before the erase.
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
    assert r.ran("modbus-flash.py", "reflex-app-1.2.0.bin") == []


def test_a_failed_flash_does_not_install_the_ui_half(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL,
                   fail={"reflex-app-1.2.0.bin"})
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


# ---------------------------------------------------------------------------
# the image-release gate (order 2026-09-14#6) -- /etc/elspi-release and
# MINIMUM_IMAGE_RELEASE. See the module docstring for why absent/unparseable
# is release 0 rather than an immediate refusal.
# ---------------------------------------------------------------------------

def test_parse_elspi_release_reads_key_value_lines():
    text = (
        "ELSPI_IMAGE_RELEASE=2\n"
        "ELSPI_IMAGE_BUILD=42\n"
        "ELSPI_IMAGE_DATE=2026-09-13\n"
        "ELSPI_REFLEX_COMMIT=714e246\n"
    )
    fields = parse_elspi_release(text)
    assert fields["ELSPI_IMAGE_RELEASE"] == "2"
    assert fields["ELSPI_REFLEX_COMMIT"] == "714e246"


def test_parse_elspi_release_skips_blanks_and_comments():
    text = "\n# a comment\nELSPI_IMAGE_RELEASE=3\nnonsense with no equals\n"
    assert parse_elspi_release(text) == {"ELSPI_IMAGE_RELEASE": "3"}


def test_read_image_release_declared(tmp_path):
    f = tmp_path / "elspi-release"
    f.write_text("ELSPI_IMAGE_RELEASE=2\n")
    got = read_image_release(f)
    assert got == ImageRelease(release=2, declared=True)


def test_read_image_release_missing_file_is_undeclared_release_zero(tmp_path):
    """Case (3)/(4) from the order: a MISSING file, never a crash."""
    got = read_image_release(tmp_path / "does-not-exist")
    assert got == ImageRelease(release=0, declared=False)


@pytest.mark.parametrize("garbage", [
    "not a key value file at all, just prose\n",
    "ELSPI_IMAGE_RELEASE=not-a-number\n",
    "ELSPI_IMAGE_BUILD=42\n",   # present file, but no ELSPI_IMAGE_RELEASE key
    "",
])
def test_read_image_release_garbage_is_undeclared_never_a_crash(tmp_path, garbage):
    """Case (5) from the order: a garbage file is treated as absent, and
    reading it must never raise."""
    f = tmp_path / "elspi-release"
    f.write_text(garbage)
    assert read_image_release(f) == ImageRelease(release=0, declared=False)


def test_gate_1_running_newer_than_minimum_is_allowed():
    """(1) release file says 2, minimum 1 -> allowed."""
    check_image_release(ImageRelease(release=2, declared=True), minimum=1)


def test_gate_2_running_older_than_minimum_refuses_naming_both_numbers():
    """(2) release file says 1, minimum 2 -> ImageTooOld naming both numbers."""
    with pytest.raises(ImageTooOld) as e:
        check_image_release(ImageRelease(release=1, declared=True), minimum=2)
    assert "1" in str(e.value)
    assert "2" in str(e.value)


def test_gate_3_absent_with_zero_minimum_is_allowed_and_logs_one_line():
    """(3) file absent, minimum 0 -> allowed and the one-line log emitted."""
    lines = []
    check_image_release(ImageRelease(release=0, declared=False), minimum=0,
                        emit=lines.append)
    assert len(lines) == 1
    assert "elspi-release" in lines[0]


def test_gate_4_absent_with_nonzero_minimum_refuses():
    """(4) file absent, minimum 1 -> refused."""
    lines = []
    with pytest.raises(ImageTooOld):
        check_image_release(ImageRelease(release=0, declared=False), minimum=1,
                            emit=lines.append)
    # the log line still fires -- the refusal names WHY, the log names WHAT
    assert len(lines) == 1


def test_gate_5_garbage_file_end_to_end_never_crashes_treated_as_absent(tmp_path):
    """(5) garbage file -> treated as absent, never a crash, end to end
    through read_image_release + check_image_release together."""
    f = tmp_path / "elspi-release"
    f.write_text("total nonsense\n")
    image = read_image_release(f)
    assert image == ImageRelease(release=0, declared=False)
    with pytest.raises(ImageTooOld):
        check_image_release(image, minimum=1)


def test_the_default_minimum_is_the_declared_constant():
    """check_image_release's default minimum tracks
    reflex.utils.image_requirement.MINIMUM_IMAGE_RELEASE, not a copy of it."""
    import inspect
    default = inspect.signature(check_image_release).parameters["minimum"].default
    assert default == MINIMUM_IMAGE_RELEASE


def test_preflight_refuses_an_image_too_old_before_touching_anything(tmp_path,
                                                                      monkeypatch):
    """Integration: the gate is wired into preflight, and fires before uv is
    even looked for -- no runner call happens at all."""
    monkeypatch.setattr(updater, "MINIMUM_IMAGE_RELEASE", 1)
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    release_file = tmp_path / "elspi-release"
    release_file.write_text("ELSPI_IMAGE_RELEASE=0\n")
    s = _session(r, tmp_path, elspi_release_path=release_file)
    with pytest.raises(ImageTooOld) as e:
        s.run(RELEASE)
    assert "1" in str(e.value)
    assert "0" in str(e.value)
    assert r.calls == [], "refused before any tool was even run"


def test_preflight_allows_an_image_at_exactly_the_minimum(tmp_path):
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    release_file = tmp_path / "elspi-release"
    release_file.write_text(f"ELSPI_IMAGE_RELEASE={MINIMUM_IMAGE_RELEASE}\n")
    s = _session(r, tmp_path, elspi_release_path=release_file)
    s.run(RELEASE)   # does not raise
    assert r.touched_the_ui_half


def test_preflight_allows_a_missing_release_file_when_minimum_is_zero(tmp_path,
                                                                       monkeypatch):
    """This is what makes today's v2026.09.13 (no /etc/elspi-release at all)
    still updatable if MINIMUM_IMAGE_RELEASE were ever shipped as 0 -- the
    fork the module docstring and CLARIFICATION describe. With the order's
    own MINIMUM_IMAGE_RELEASE = 1 this same machine is REFUSED (proven by
    test_preflight_refuses_an_image_too_old_before_touching_anything above,
    where an explicit release=0 file stands in for "undeclared"); this test
    pins the minimum=0 branch of the arithmetic in isolation by patching the
    module constant directly, without needing a second real fixture file.
    """
    monkeypatch.setattr(updater, "MINIMUM_IMAGE_RELEASE", 0)
    r = FakeRunner(board_protocol_after=TARGET_PROTOCOL)
    s = _session(r, tmp_path, elspi_release_path=tmp_path / "does-not-exist")
    s.run(RELEASE)   # does not raise
    assert r.touched_the_ui_half


# --------------------------------------------------------------------------
# last_flashed_rev -- the manifest reader behind the Backup screen's meta.fw
# --------------------------------------------------------------------------
# Record shapes copied from elspi's real ~/firmware/flashed.json (2026-09-17).
_FLASH = ('{"utc":"2026-09-13T15:08:45Z","variant":"unknown","rev":"cb52073",'
          '"via":"modbus","image":"reflex-app-cb52073.bin","protocol":10}')
_RELEASE = ('{"utc":"2026-09-13T15:51:51Z","variant":"release","rev":"43ac7c5",'
            '"via":"modbus","image":"reflex-app-1.2.0-rc.3.bin","protocol":10,"tag":"v1.2.0-rc.3"}')
_REVERT = ('{"utc":"2026-09-13T01:57:21Z","variant":"revert","rev":"88e57ec",'
           '"md5":null,"via":"modbus","protocol":10,"reverted_from":"cb52073"}')


def _manifest(tmp_path, *lines):
    path = tmp_path / "flashed.json"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_last_flashed_rev_is_the_last_record_with_its_tag(tmp_path):
    assert updater.last_flashed_rev(_manifest(tmp_path, _FLASH, _RELEASE)) == "43ac7c5 (v1.2.0-rc.3)"


def test_last_flashed_rev_follows_a_revert_to_the_rev_it_restored(tmp_path):
    assert updater.last_flashed_rev(_manifest(tmp_path, _RELEASE, _REVERT)) == "88e57ec"


def test_last_flashed_rev_skips_a_torn_last_line(tmp_path):
    assert updater.last_flashed_rev(_manifest(tmp_path, _FLASH, '{"utc":"2026-09-1')) == "cb52073"


def test_last_flashed_rev_is_none_without_a_manifest(tmp_path):
    assert updater.last_flashed_rev(tmp_path / "absent.json") is None
    assert updater.last_flashed_rev(_manifest(tmp_path, "")) is None
