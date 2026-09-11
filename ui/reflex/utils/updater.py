"""In-app update: the fw+ui half of a lockstep release, applied at the machine.

WHAT AN UPDATE IS HERE. A reflex release (``.github/workflows/release.yml``) is
ONE version, ONE tag, BOTH halves -- so "update to v1.2.0" means the firmware
running on the STM32 and the UI running on the Pi both become v1.2.0, or
neither does. This module is the machine-side execution of that, and it exists
because ``fw/scripts/modbus-flash.py`` made the firmware half possible without
an ST-Link: the app reboots into the sector-0 bootloader over the RS-485 link
the UI already holds, and the whole flash is ~13 s.

THE ORDER IS FIRMWARE FIRST, AND THAT IS A SAFETY ARGUMENT, not a convenience.
Neither ordering is symmetric:

  * UI first would leave a NEW UI talking to OLD firmware if the flash then
    failed -- and the operator has no terminal, so the only thing that can fix
    it is the UI that is now mismatched.
  * Firmware first can leave NEW firmware under the OLD UI if the git/uv half
    then fails -- but that state still has a working UI on the screen, and the
    firmware half is the RECOVERABLE one: the bootloader is resident,
    independent of the app, and ``modbus-flash.py`` can be pointed at any
    image, including the previous release's, from that same UI.

Recoverable-half-first is why the sequence is preflight, flash, GATE, then git.

THE GATE (``verify_firmware_half``) is the load-bearing part and the reason
this feature was allowed back at all. The bootloader's anti-brick swap-back
covers a firmware image that will not RUN. It does not cover, and cannot
notice, a firmware image that runs perfectly while speaking a different
register layout from the UI -- which is exactly the fault the lockstep release
model exists to prevent, and exactly the fault a full updater is uniquely able
to create. So the UI half is not installed on a warning; it is not installed at
all unless the flashed application has been OBSERVED to report an
``idAppProtocolVersion`` equal to the ``ELS_PROTOCOL_VERSION`` of the UI about
to be checked out. There is no confirm dialog on that check and no code path to
:meth:`UpdateSession.install_ui_half` that does not carry a
:class:`FirmwareVerdict` produced by it.

Note which two numbers are compared, because the obvious pair is the wrong
pair. Comparing the flashed firmware against the RUNNING UI's
``ELS_PROTOCOL_VERSION`` would pass whenever the release did not move the
protocol and fail whenever it did -- i.e. it would refuse precisely the updates
that are correct. The target UI's value is read out of the tag itself, with
``git show <tag>:ui/reflex/utils/els_stop_map.py``, before anything is checked
out. That is the GENERATED register map, not ``devices.py``: devices.py's
``ELS_PROTOCOL_VERSION`` is an alias (``= els_stop_map.PROTOCOL_VERSION``) and
so is not a literal a text scan can read, whereas els_stop_map.py is written by
``tools/genregs.py`` from ``registers/els_stop.yaml`` and states
``PROTOCOL_VERSION = <int>`` as a bare literal by construction.

WHAT CANNOT BE PRE-CHECKED. The firmware image header (``fw/scripts/
reflex_image.py``: magic, header version, length, CRC32, build rev) carries no
protocol version, so the firmware's half of the comparison is only knowable by
flashing it and asking the identity window. That is not a gap in the check --
it is why the gate sits AFTER the flash and before the git operations, rather
than being a preflight.

EVERY EXTERNAL EFFECT IS INJECTED (``runner``, ``download``, ``emit``) so the
whole sequence, gate included, is exercised in tests without a board, a
network, or a git checkout. The defaults in :mod:`reflex.utils.updater` bind
those to subprocess and urllib.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from kivy.logger import Logger

log = Logger.getChild(__name__)

# The LIVE repo. Funkenjaeger/reflex-ui, which this screen queried until the
# 2026-09-07 rebuild, is ARCHIVED: its newest non-prerelease tag is v1.0.0,
# which is what elspi logged as "[Selected release]" on every boot for weeks.
GITHUB_RELEASES_URL = "https://api.github.com/repos/Funkenjaeger/reflex/releases"

# FETCH OVER HTTPS, NOT THE CHECKOUT'S OWN `origin`, and this is about who the
# operator is rather than about tidiness. `origin` is whatever provisioning
# happened to set: on elspi it is `git@github.com-reflex:...`, an SSH alias
# defined in the `default` user's ~/.ssh/config against a read-only deploy key.
# reflex-ui runs as ROOT, which has no such config, so `git fetch origin` dies
# with "Could not resolve hostname github.com-reflex" -- observed on the machine
# 2026-09-07, on the updater's first real run.
#
# Fixing that by giving root the key would only make it work for a machine
# somebody provisioned with a deploy key. A user of this lathe is not a
# developer and will never have a GitHub SSH key at all, so an updater that
# needs one is not an updater for them (Evan, 2026-09-07). The repo is public
# and the firmware asset is already downloaded over plain HTTPS, so the git half
# now matches: anonymous, credential-free, and independent of how the checkout
# was cloned.
#
# If reflex ever goes private this breaks, loudly, and the fix is a token --
# not a silent fallback to `origin`.
GITHUB_FETCH_URL = "https://github.com/Funkenjaeger/reflex.git"

RELEASE_LIST_LIMIT = 10

# THE SLOTTED IMAGE, AND ONLY IT. A release publishes two firmware binaries
# built from the same source, and exactly one of them can be delivered over the
# wire:
#
#   reflex-app-<version>.bin  linked at the bootloader's RUN slot 0x08020000
#                             with the RFLX header at +0x200. modbus-flash.py
#                             takes this and nothing else.
#   reflex-fw-<version>.bin   the legacy 0x08000000 monolith for SWD recovery
#                             (fw/scripts/flash.sh). No header; the bootloader
#                             refuses it, and preflight below refuses it first.
#
# This pattern matched `reflex-fw-` until 2026-09-07, when release.yml began
# publishing both -- which is also why nothing before then is installable from
# the machine at all: those releases carry only the legacy image. Picking the
# wrong one is not dangerous (preflight's reflex_image.py check stops it before
# the erase) but it is the whole class of mistake the two prefixes exist to
# prevent, so the pattern is anchored and shares no prefix with the legacy name
# rather than being a `reflex-fw-` glob narrowed by a suffix.
FIRMWARE_ASSET_RE = re.compile(r"^reflex-app-.+\.bin$")

SERVICE_NAME = "reflex-ui.service"


class UpdateRefused(Exception):
    """The update did not happen, and the machine was not left half-updated."""


class ProtocolMismatch(UpdateRefused):
    """The flashed firmware does not speak the target UI's register layout.

    Its own class because it is the one refusal that can fire AFTER the board
    has been written to, and so the one whose message has to tell the operator
    what state the machine is actually in.
    """


# --------------------------------------------------------------------------
# Release list
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Release:
    tag: str
    prerelease: bool
    firmware_url: str
    firmware_name: str

    @property
    def version(self) -> str:
        """The tag without its leading v -- how release.yml names assets."""
        return self.tag[1:] if self.tag.startswith("v") else self.tag


def select_releases(payload, *, allow_prerelease: bool,
                    limit: int = RELEASE_LIST_LIMIT) -> list[Release]:
    """Installable releases from the GitHub API payload, newest first.

    THREE FILTERS, and each one drops something that would otherwise be
    offered and could not be installed:

    * drafts are not published and have no downloadable assets;
    * a release with no ``reflex-app-*.bin`` asset cannot supply the firmware
      half, so under the fw+ui model it is not an update at all. This is a
      real exclusion, not a defensive one: every release before 2026-09-07
      predates the slotted image format and published only the legacy
      ``reflex-fw-*.bin``, which the bootloader cannot flash;
    * pre-releases unless ``allow_prerelease``.

    ``allow_prerelease`` is what the screen's "experimental versions" toggle
    now means. It used to add a "dev (experimental)" entry that tracked the
    dev BRANCH -- a UI-only git checkout with no firmware image anywhere, i.e.
    precisely the UI-only update the fw+ui decision rejected. A pre-release tag
    is the honest replacement: it is built by the same lockstep workflow and
    carries both halves.
    """
    out = []
    for item in payload:
        if item.get("draft"):
            continue
        if item.get("prerelease") and not allow_prerelease:
            continue
        asset = next((a for a in item.get("assets", [])
                      if FIRMWARE_ASSET_RE.match(a.get("name", ""))), None)
        if asset is None:
            continue
        out.append(Release(
            tag=item["tag_name"],
            prerelease=bool(item.get("prerelease")),
            firmware_url=asset["browser_download_url"],
            firmware_name=asset["name"],
        ))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# Parsers for the two fw/scripts tools
#
# These are the seam between the UI and scripts this task may not modify. Each
# one FAILS LOUD on an unrecognised line rather than returning a default: an
# unparsed identity is the input to the protocol gate, and a gate that treats
# "I could not read the board" as "the board is fine" is not a gate.
# --------------------------------------------------------------------------

_IDENTITY_RE = re.compile(
    r"stage=(?P<stage>\S+)\s+windowVersion=(?P<window>\d+)\s+"
    r"rev=(?P<rev>\S+)\s+appProtocol=(?P<protocol>\d+)")

STAGE_APPLICATION = "application"
STAGE_BOOTLOADER = "bootloader"


@dataclass(frozen=True)
class Identity:
    """One reading of the identity window at ELS_ID_BASE (2048).

    Served by BOTH stages, which is the point: ``stage`` says whether the
    bootloader or the application answered, and ``app_protocol`` is meaningful
    only when the application did.
    """
    stage: str
    build_rev: str
    app_protocol: int

    def __str__(self) -> str:
        return (f"{self.stage} rev {self.build_rev} "
                f"protocolVersion {self.app_protocol}")


def parse_identity(text: str) -> Identity:
    """Read ``modbus-flash.py --identity`` output.

    Its format is ``Identity.__str__`` in that script:
    ``idMagic=0x454c stage=application windowVersion=1 rev=fe9e8dc appProtocol=9``
    """
    m = _IDENTITY_RE.search(text)
    if not m:
        raise UpdateRefused(
            "Could not read the controller's identity window. The board did "
            "not answer in a form this UI recognises, so its firmware version "
            "is UNKNOWN -- refusing rather than guessing.\n"
            f"Output was: {text.strip()[:400]}")
    return Identity(stage=m.group("stage"), build_rev=m.group("rev"),
                    app_protocol=int(m.group("protocol")))


_IMAGE_INFO_RE = re.compile(r"rev (?P<rev>[0-9a-f]+(?:-dirty)?), (?P<len>\d+) bytes")


@dataclass(frozen=True)
class ImageInfo:
    rev: str
    length: int


def parse_image_info(text: str) -> ImageInfo:
    """Read ``reflex_image.py info`` output for a VALID image.

    The script exits 1 and prints ``INVALID:`` for a bad one; callers check
    the exit status first, so reaching here with an unparseable line means
    something changed under us and is refused rather than defaulted.
    """
    m = _IMAGE_INFO_RE.search(text)
    if not m:
        raise UpdateRefused(
            "The downloaded firmware image did not describe itself in a form "
            "this UI recognises; refusing to flash it.\n"
            f"Output was: {text.strip()[:400]}")
    return ImageInfo(rev=m.group("rev"), length=int(m.group("len")))


# Anchored, and the value must be a bare integer that ENDS the statement -- an
# optional trailing comment is all that may follow. Anything else (an alias, an
# expression, a suffixed token) fails to match and is refused rather than
# half-read. ``^`` also keeps this off devices.py's ``ELS_PROTOCOL_VERSION``,
# which is a different name with a different, non-literal right-hand side.
_PROTOCOL_RE = re.compile(r"^PROTOCOL_VERSION\s*=\s*(\d+)\s*(?:#.*)?$", re.M)


def parse_protocol_version(map_source: str) -> int:
    """``PROTOCOL_VERSION`` out of a ``reflex/utils/els_stop_map.py`` SOURCE text.

    THE GENERATED MAP, NOT ``devices.py``. devices.py exports the same number
    as ``ELS_PROTOCOL_VERSION``, but as an alias -- ``ELS_PROTOCOL_VERSION =
    els_stop_map.PROTOCOL_VERSION`` -- and an alias is not something a text
    scan can resolve. els_stop_map.py is emitted by ``tools/genregs.py`` from
    ``registers/els_stop.yaml`` and says ``PROTOCOL_VERSION = <int>``, a bare
    literal, because a generator wrote it; that form is stable by construction
    rather than by convention, which is the whole reason the scan points here.

    Deliberately a text scan and not an import. The source being read is the
    TARGET release's, fetched with ``git show <tag>:...`` while a different
    version of this same module is running; importing it would either collide
    in ``sys.modules`` or execute code from a tag nobody has reviewed yet, on a
    machine, to answer a question one regex answers.
    """
    m = _PROTOCOL_RE.search(map_source)
    if not m:
        raise UpdateRefused(
            "Could not read PROTOCOL_VERSION as a plain number from the target "
            "release's ui/reflex/utils/els_stop_map.py. Without it there is "
            "nothing to check the firmware against, so the update is refused "
            "and nothing has been changed. The machine is safe to keep using "
            "on the version it is running; update from the command line "
            "instead -- see the Installing page.")
    return int(m.group(1))


# --------------------------------------------------------------------------
# Where we are installed
# --------------------------------------------------------------------------

def resolve_checkout(start: Path | None = None) -> Path:
    """The monorepo checkout this running UI lives in.

    DERIVED, never a literal. The screen used to install into ``/reflex-ui``,
    a path deleted from elspi at the 2026-08-25 monorepo cutover, and the only
    reason that was a dead feature rather than a downgrade hazard is that the
    literal failed safe. Restating it as ``/home/default/projects/reflex``
    would rebuild the same fault with a fresher constant -- ``deploy/start.sh``
    has resolved its own location since the weld, and this does the same:
    ``<root>/ui/reflex/utils/updater.py`` -> ``<root>``.

    Raises :class:`UpdateRefused` when the result is not a checkout that can be
    updated -- an installed wheel, a copied tree, a directory with no ``fw/``.
    That IS the honest answer for those, and it is better to say so at the
    preflight than to find out after the board has been written to.
    """
    here = Path(start or __file__).resolve()
    root = here.parents[3]          # utils -> reflex -> ui -> <root>
    missing = [str(p) for p in (root / ".git",
                                root / "ui" / "pyproject.toml",
                                root / "fw" / "scripts" / "modbus-flash.py",
                                root / "fw" / "scripts" / "reflex_image.py")
               if not p.exists()]
    if missing:
        raise UpdateRefused(
            f"This UI is not running from a reflex monorepo git checkout "
            f"({root} is missing {', '.join(missing)}). In-app update needs "
            f"one for both halves: the git tag for the UI and fw/scripts for "
            f"the firmware. Update from the command line instead -- see the "
            f"Installing page.")
    return root


def find_uv(env_path: str | None = None) -> str:
    """The ``uv`` binary that will sync the venv, or refuse.

    Checked at PREFLIGHT rather than discovered after the flash. uv installs to
    ``~/.local/bin`` and the service runs as root, so it is routinely not on
    the service's PATH even though it is on the machine -- finding that out
    between the flash and the checkout is how a machine ends up half-updated.
    """
    found = shutil.which("uv", path=env_path)
    if found:
        return found
    for candidate in ("/home/default/.local/bin/uv", "/root/.local/bin/uv",
                      "/usr/local/bin/uv", "/usr/bin/uv"):
        if os.access(candidate, os.X_OK):
            return candidate
    raise UpdateRefused(
        "uv was not found, and the UI half of an update cannot be installed "
        "without it. Install uv (or put it on the service's PATH) and try "
        "again -- nothing has been changed.")


def manifest_path_for(checkout: Path) -> Path:
    """``<home of the checkout's OWNER>/firmware/flashed.json``.

    The flash manifest is the login user's file: ot-state reads it from
    ``/home/default/firmware``, and ``flash.sh`` run by that user appends to it.
    This process is reflex-ui, which runs as ROOT, so its own ``~`` is
    ``/root`` -- a record written there is a record nothing ever reads, and it
    would look exactly like success. The checkout's owner is the user this
    machine was installed as, which is the home ``fw/scripts`` belongs to.
    Same trap as the fetch-over-origin one (``test_the_fetch_never_uses_the_
    checkouts_own_remote``): root is not the user whose files these are.
    """
    import pwd   # POSIX only; imported here so the module still loads on Windows
    owner = Path(checkout).stat().st_uid
    return Path(pwd.getpwuid(owner).pw_dir) / "firmware" / "flashed.json"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FirmwareVerdict:
    """Proof that a SPECIFIC flashed firmware matches a SPECIFIC target UI.

    Exists as a type so that "the firmware half was verified" is something the
    UI installer can require an argument for, rather than a boolean somebody
    can forget to check. :func:`verify_firmware_half` is the only thing that
    builds one, and :meth:`UpdateSession.install_ui_half` re-asserts its
    internal consistency before it runs a single git command -- so a
    hand-constructed verdict has to be a truthful one to get past.
    """
    identity: Identity
    target_protocol: int
    target_tag: str


def verify_firmware_half(identity: Identity, target_protocol: int,
                         target_tag: str, expected_rev: str | None = None
                         ) -> FirmwareVerdict:
    """THE GATE. Refuse -- do not warn -- unless the flashed firmware matches.

    Three conditions, all of which must hold:

    1. the APPLICATION is running (``stage``), not the bootloader. A board
       sitting in the bootloader has no application to be paired with;
    2. its build revision is the one we just flashed, when the caller knows
       what that was. This catches an apply that silently did not take, and
       the bootloader's own swap-back reverting to the previous image;
    3. ``idAppProtocolVersion`` equals the TARGET UI's
       ``ELS_PROTOCOL_VERSION``. Not the running UI's -- see the module
       docstring for why that comparison would refuse exactly the correct
       updates.

    Raises :class:`ProtocolMismatch` naming the state the machine is in,
    because this is the one refusal that fires after the board has been
    written to.
    """
    if identity.stage != STAGE_APPLICATION:
        raise ProtocolMismatch(
            f"After flashing, the controller is in the {identity.stage}, not "
            f"the application. The UI half of {target_tag} was NOT installed. "
            f"The controller still has a bootloader you can re-flash from.")

    if expected_rev is not None and identity.build_rev != expected_rev:
        raise ProtocolMismatch(
            f"After flashing, the controller reports build {identity.build_rev} "
            f"but the image was {expected_rev}. Either the new image did not "
            f"take or the bootloader reverted to the previous one. The UI half "
            f"of {target_tag} was NOT installed, so the two halves still match "
            f"each other.")

    if identity.app_protocol != target_protocol:
        raise ProtocolMismatch(
            f"REFUSED: the firmware now on the controller speaks register "
            f"protocol version {identity.app_protocol}, but the {target_tag} "
            f"UI expects {target_protocol}. Installing it would produce "
            f"exactly the mismatch that lockstep releases exist to prevent -- "
            f"every register past the point of divergence would read as "
            f"plausible nonsense. The UI half was NOT installed.\n"
            f"The controller is running {target_tag} firmware under the "
            f"previous UI. Recover by re-flashing the previous release's "
            f"firmware, or by updating both halves from the command line.")

    return FirmwareVerdict(identity=identity, target_protocol=target_protocol,
                           target_tag=target_tag)


# --------------------------------------------------------------------------
# Default I/O bindings
# --------------------------------------------------------------------------

def subprocess_runner(argv, cwd=None, timeout=None, emit=None):
    """Run a command, streaming its output to ``emit``. Returns (rc, output).

    Combined stdout+stderr, because everything run here is a tool whose
    progress narration and its errors are equally worth showing the operator
    on a machine with no terminal.
    """
    p = subprocess.Popen([str(a) for a in argv], cwd=str(cwd) if cwd else None,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    lines = []
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        if emit:
            emit(line)
    p.wait(timeout=timeout)
    return p.returncode, "\n".join(lines)


def urllib_download(url: str, dest: Path) -> Path:
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def urllib_fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------
# The sequence
# --------------------------------------------------------------------------

@dataclass
class Prepared:
    """Everything preflight established, before anything was changed."""
    release: Release
    image_path: Path
    image: ImageInfo
    target_protocol: int
    uv: str


class UpdateSession:
    """One update attempt. Synchronous by design; the screen runs it off-thread.

    All I/O is injected so the sequence -- preflight, flash, gate, UI half --
    is testable end to end with no board, no network and no git.
    """

    def __init__(self, *, checkout: Path, port: str, current_protocol: int,
                 workdir: Path, runner=subprocess_runner,
                 download=urllib_download, fetch_json=urllib_fetch_json,
                 emit=None, uv_finder=find_uv, service: str = SERVICE_NAME,
                 python: str | None = None, restart=None,
                 manifest: Path | None = None):
        self.checkout = Path(checkout)
        self._manifest = manifest
        self.port = port
        self.current_protocol = current_protocol
        self.workdir = Path(workdir)
        self._runner = runner
        self._download = download
        self._fetch_json = fetch_json
        self._emit = emit or (lambda line: None)
        self._uv_finder = uv_finder
        self.service = service
        self.python = python or sys.executable
        self._restart = restart or self._systemctl_restart

    # -- helpers ----------------------------------------------------------

    def emit(self, line: str):
        self._emit(line)
        log.info(f"update: {line}")

    def _run(self, argv, cwd=None, timeout=None, what="", quiet=False):
        """``quiet`` suppresses the live echo, not the capture.

        For commands whose OUTPUT is data rather than progress -- ``git show``
        of a source file being the one that matters, since streaming it would
        dump the whole of els_stop_map.py into the operator's status box.
        """
        rc, out = self._runner(argv, cwd=cwd, timeout=timeout,
                               emit=None if quiet else self._emit)
        if rc != 0:
            raise UpdateRefused(
                f"{what or ' '.join(str(a) for a in argv)} failed (exit {rc}).\n"
                f"{out.strip()[-600:]}")
        return out

    @property
    def _modbus_flash(self) -> Path:
        return self.checkout / "fw" / "scripts" / "modbus-flash.py"

    @property
    def _reflex_image(self) -> Path:
        return self.checkout / "fw" / "scripts" / "reflex_image.py"

    def read_identity(self) -> Identity:
        """Read the identity window. The caller must already own the port."""
        out = self._run([self.python, self._modbus_flash, "--identity",
                         "--port", self.port], timeout=60,
                        what="reading the controller identity")
        return parse_identity(out)

    def list_releases(self, *, allow_prerelease: bool) -> list[Release]:
        payload = self._fetch_json(GITHUB_RELEASES_URL)
        return select_releases(payload, allow_prerelease=allow_prerelease)

    # -- 1. preflight ------------------------------------------------------

    def preflight(self, release: Release) -> Prepared:
        """Establish that the update CAN succeed, changing nothing.

        Everything here is reversible-by-doing-nothing: reads, a download into
        a temp directory, and ``git fetch``, which moves refs and never touches
        the work tree. The first irreversible act in the whole sequence is the
        erase in :meth:`flash_firmware`.
        """
        self.emit(f"Preparing {release.tag}.")

        uv = self._uv_finder()
        self.emit(f"uv: {uv}")

        dirty = self._run(["git", "status", "--porcelain"], cwd=self.checkout,
                          quiet=True, what="git status").strip()
        if dirty:
            raise UpdateRefused(
                "The checkout has uncommitted changes, so checking out "
                f"{release.tag} could fail halfway or discard work. Nothing "
                f"has been changed.\n{dirty[:400]}")

        self.emit(f"Fetching tags from {GITHUB_FETCH_URL}.")
        self._run(["git", "fetch", "--tags", "--force", GITHUB_FETCH_URL],
                  cwd=self.checkout, timeout=300, what="git fetch")
        self._run(["git", "rev-parse", "--verify", f"{release.tag}^{{commit}}"],
                  cwd=self.checkout, what=f"resolving tag {release.tag}")

        # The target UI's expectation, read out of the tag itself. This is the
        # number the gate compares the flashed firmware against. The generated
        # register map is the source of it -- see parse_protocol_version.
        source = self._run(
            ["git", "show", f"{release.tag}:ui/reflex/utils/els_stop_map.py"],
            cwd=self.checkout, quiet=True,
            what=f"reading els_stop_map.py from {release.tag}")
        target_protocol = parse_protocol_version(source)
        self.emit(f"{release.tag} UI expects register protocol version "
                  f"{target_protocol} (this UI: {self.current_protocol}).")

        self.workdir.mkdir(parents=True, exist_ok=True)
        image_path = self.workdir / release.firmware_name
        self.emit(f"Downloading {release.firmware_name}.")
        self._download(release.firmware_url, image_path)

        # The authoritative validator, not a reimplementation: reflex_image.py
        # mirrors blImageValidate() in the bootloader, so an image this rejects
        # is one the board would reject too -- found here, before the erase.
        rc, out = self._runner([self.python, self._reflex_image, "info", image_path],
                               cwd=None, timeout=60, emit=self._emit)
        if rc != 0:
            raise UpdateRefused(
                f"The {release.tag} firmware asset is not a slotted "
                f"application image, so it cannot be flashed over Modbus. "
                f"This release cannot be installed from the machine; update "
                f"from the command line instead.\n{out.strip()[:400]}")
        image = parse_image_info(out)
        self.emit(f"Firmware image: rev {image.rev}, {image.length} bytes.")

        return Prepared(release=release, image_path=image_path, image=image,
                        target_protocol=target_protocol, uv=uv)

    # -- 2. flash, then THE GATE ------------------------------------------

    def flash_firmware(self, prepared: Prepared) -> FirmwareVerdict:
        """Flash the controller and verify what came back. Owns the port.

        The caller must have stopped the UI's polling first -- see
        :meth:`reflex.dispatchers.board.Board.pause_polling`.
        """
        before = self.read_identity()
        self.emit(f"Controller before: {before}.")
        if before.stage != STAGE_APPLICATION:
            raise UpdateRefused(
                f"The controller is in the {before.stage}, not running an "
                f"application. Recover it with fw/scripts/modbus-flash.py "
                f"before updating.")
        if before.app_protocol != self.current_protocol:
            raise UpdateRefused(
                f"This machine is ALREADY mismatched: the controller speaks "
                f"protocol version {before.app_protocol} and the running UI "
                f"expects {self.current_protocol}. Fix that before updating, "
                f"so that a failed update cannot be blamed on it. Nothing has "
                f"been changed.")

        self.emit(f"Flashing {prepared.release.tag} firmware. "
                  f"DO NOT POWER OFF THE MACHINE.")
        # modbus-flash.py records the flash in the manifest once the board
        # reports the new rev. The path is explicit because this runs as
        # root -- see manifest_path_for.
        manifest = self._manifest or manifest_path_for(self.checkout)
        self._run([self.python, self._modbus_flash, prepared.image_path,
                   "--port", self.port, "--manifest", manifest,
                   "--record-variant", "release",
                   "--record-tag", prepared.release.tag], timeout=600,
                  what="flashing the controller")

        after = self.read_identity()
        self.emit(f"Controller after: {after}.")
        verdict = verify_firmware_half(
            after, prepared.target_protocol, prepared.release.tag,
            expected_rev=prepared.image.rev)
        self.emit(f"Firmware half verified: protocol version "
                  f"{verdict.identity.app_protocol} matches the "
                  f"{verdict.target_tag} UI.")
        return verdict

    # -- 3. the UI half, reachable only with a verdict ---------------------

    def install_ui_half(self, prepared: Prepared, verdict: FirmwareVerdict):
        """Check out the tag, sync the venv, restart the service.

        Takes the verdict as an argument rather than consulting a flag, and
        re-asserts it here: the gate is a precondition of this method, not a
        step somebody can reorder around.
        """
        if not isinstance(verdict, FirmwareVerdict):
            raise UpdateRefused("install_ui_half requires a verified firmware "
                                "half; refusing.")
        if (verdict.target_tag != prepared.release.tag
                or verdict.target_protocol != prepared.target_protocol
                or verdict.identity.app_protocol != prepared.target_protocol):
            raise ProtocolMismatch(
                "The firmware verdict does not describe this update "
                f"({verdict.target_tag}/{verdict.target_protocol} vs "
                f"{prepared.release.tag}/{prepared.target_protocol}); refusing.")

        self.emit(f"Checking out {prepared.release.tag}.")
        self._run(["git", "checkout", "--detach", prepared.release.tag],
                  cwd=self.checkout, timeout=300,
                  what=f"git checkout {prepared.release.tag}")

        self.emit("Syncing the Python environment (this can take a while).")
        self._run([prepared.uv, "sync", "--frozen"],
                  cwd=self.checkout / "ui", timeout=3600, what="uv sync")

        self.emit(f"Restarting {self.service}. The UI will come back on "
                  f"{prepared.release.tag}.")
        self.restart_service()

    def restart_service(self):
        self._restart()

    def _systemctl_restart(self):
        """Detached on purpose: systemd kills this process as part of the
        restart, so waiting on the command would mean waiting to be killed.

        NOT routed through ``self._runner`` for the same reason -- and
        separately injectable so that a test of the install sequence cannot
        restart the developer's machine by getting one argument wrong."""
        subprocess.Popen(["systemctl", "restart", self.service],
                         start_new_session=True)

    # -- the whole thing ---------------------------------------------------

    def run(self, release: Release, *, pause_link=None, resume_link=None):
        """Preflight, flash, gate, install. Raises :class:`UpdateRefused`.

        ``pause_link``/``resume_link`` hand the serial port over and take it
        back; the port stays with the flasher from before the first identity
        read until the service restarts, so no old-UI poll ever lands on new
        firmware.

        THE FAILURE PATHS RESUME THE LINK, including the one that leaves new
        firmware under the old UI. That state is a mismatch, and it is
        tempting to leave the link down rather than "let the UI talk to
        firmware it does not match" -- but the operator has no terminal, and a
        machine with a dead DRO tells them nothing. Board's connect-time
        protocol check is the surface built for exactly this: it names which
        half is older, in words, on the screen. Leaving the link down would
        suppress the one message that explains what happened.
        """
        prepared = self.preflight(release)
        if pause_link:
            pause_link()
        try:
            verdict = self.flash_firmware(prepared)
        except Exception:
            if resume_link:
                resume_link()
            raise
        try:
            self.install_ui_half(prepared, verdict)
        except Exception:
            if resume_link:
                resume_link()
            raise
