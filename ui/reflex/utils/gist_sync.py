"""Opt-in sync of the commissioning bundle to the operator's OWN GitHub gist.

OFF BY DEFAULT, AND OFF IS THE ABSENCE OF A FILE. Nothing here runs unless the
operator turned the toggle on in Setup, which is recorded by creating
:func:`enabled_path`. No token, no gist, no request until then.

WHY A GIST AND NOT A SERVICE. The bundle (``commissioning_bundle.build``) is
the whole machine as one YAML document -- the same document the USB export
carries. The thing it protects against is a dead SD card, which is exactly the
event that also destroys every local snapshot. Somewhere off the machine is the
whole requirement; a secret gist in the operator's OWN account is the cheapest
such place that needs no server, no account we run, and no credential we hold.
It is THEIR data in THEIR account, revocable by them at
``github.com/settings/applications``, which the Setup screen says out loud.

WHY THE DEVICE FLOW. The lathe has a 1024x600 touchscreen, no keyboard worth
typing a password on, and no browser. The OAuth device flow is the grant
designed for exactly that: the machine shows an eight-character code, the
operator types it on their phone, and the machine polls until it is told yes.
It needs a PUBLIC client id and NO client secret -- there is no secret to leak
here, which is the other reason it is the right grant for a device whose
filesystem the operator can read.

:data:`GIST_CLIENT_ID` is a real, public, device-flow-only OAuth app id. A
device-flow client id is public by design (it ships in every copy of the app
and is visible in the request the operator's browser makes), so committing it
is not a disclosure. A fork that has not registered its own app sets it to
``None`` and the feature reports itself "not configured" rather than failing at
the first request.

WHERE THE TOKEN IS NOT. **Not under** :func:`reflex.utils.paths.config_dir`.
That directory is exported wholesale by the USB bundle, captured by the ledger
snapshots, and pulled by the nightly backup -- a token placed there would be
copied onto every USB stick and into every gist this module itself writes. It
lives in :func:`state_dir` (``~/.local/state/reflex``), mode 0600, created with
``os.open(..., 0o600)`` so it is never briefly world-readable between
``open()`` and ``chmod``. ``test_gist_sync.py`` asserts both the location and
that ``build()`` output never contains it.

THE ONE NETWORK SEAM is :func:`http_json`. Every request in this module goes
through it and nothing else imports ``urllib``; tests inject a fake in its
place and the suite never contacts GitHub. Stdlib ``urllib.request`` on
purpose: ``ui/pyproject.toml`` gained nothing for this feature.

FAILURE POLICY. A sync failure is logged and dropped. It is never raised into
the UI thread and never retried in a loop -- the retry is the NEXT
commissioning change, which is when the bundle changed anyway and so is the
only moment a retry has anything new to say. A machine with no network keeps
cutting metal; it just has a stale gist.
"""
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml
from kivy.logger import Logger

from reflex.utils import commissioning_bundle
from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)

#: Evan's PUBLIC device-flow OAuth App client id. No client secret exists for
#: it and none is needed -- see the module docstring. Set to ``None`` in a fork
#: that has not registered its own app: every entry point then reports
#: :data:`NOT_CONFIGURED_MESSAGE` and makes no request at all.
GIST_CLIENT_ID = "Ov23libizt0tOMvxAojE"

#: The ONLY scope asked for. ``gist`` grants read/write of the user's gists and
#: nothing else -- not repos, not their profile, not their org membership.
SCOPE = "gist"

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GISTS_URL = "https://api.github.com/gists"

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

#: Shown wherever the feature is reached with :data:`GIST_CLIENT_ID` unset.
NOT_CONFIGURED_MESSAGE = (
    "Gist sync is not configured in this build (no OAuth client id).")

#: Where the operator turns the grant off again. Displayed next to the toggle
#: because an OAuth grant the user cannot find is an OAuth grant they cannot
#: revoke.
REVOKE_URL = "https://github.com/settings/applications"

#: The single file inside the gist. Fixed rather than timestamped: a gist keeps
#: its own revision history, so a new filename per sync would turn one file
#: with N revisions into N files with one revision each and lose the diff.
BUNDLE_FILENAME = "reflex-commissioning.yaml"

#: A gist this machine owns is recognised by its description. The machine-id
#: follows the prefix, which is what :func:`list_machine_gists` matches on and
#: what makes a restore able to tell two lathes apart in one account.
DESCRIPTION_PREFIX = "reflex commissioning bundle: "

#: Override for the whole state directory (token + gist id + the enabled
#: marker). A sibling of ``REFLEX_CONFIG_DIR`` in spirit, kept separate on
#: purpose: pointing this at the config dir is the exact mistake the module
#: exists to avoid, and a test asserts it is not the default.
STATE_DIR_ENV = "REFLEX_STATE_DIR"

TOKEN_NAME = "github-token"
GIST_ID_NAME = "gist-id"
ENABLED_NAME = "gist-sync-enabled"

#: Mode for the token file. 0600: the service user only.
TOKEN_MODE = 0o600

#: GitHub's documented bump when it answers ``slow_down``. The response also
#: carries an ``interval``; whichever is larger wins (see :meth:`DeviceFlow.poll`).
SLOW_DOWN_BUMP = 5


class GistSyncError(Exception):
    """A device-flow or gist operation that ended for a NAMED reason.

    ``str(e)`` is written for the operator, not for a log reader: it is put
    straight on the Setup screen's status line.
    """


class NotConfigured(GistSyncError):
    """:data:`GIST_CLIENT_ID` is ``None``. Raised BEFORE any request."""

    def __init__(self, message: str = NOT_CONFIGURED_MESSAGE):
        super().__init__(message)


# ── the one network seam ────────────────────────────────────────────────────

def http_json(url: str, *, method: str = "GET", data: dict | None = None,
              token: str | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    """Do ONE HTTP request and return ``(status, parsed_json)``.

    THE ONLY PLACE THIS MODULE TOUCHES THE NETWORK. Everything else takes a
    ``transport`` argument defaulting to this function, which is the seam the
    tests replace -- so the suite can exercise every branch of the device flow
    and of the gist create/patch split without a socket.

    ``data`` is sent as a JSON body; GitHub's device endpoints accept JSON and
    answer JSON when asked to (``Accept: application/json``), which spares this
    module a form-encoder and a ``application/x-www-form-urlencoded`` parser.

    A non-2xx answer is RETURNED, not raised -- the device flow's
    ``authorization_pending`` arrives as HTTP 200 with an ``error`` key, but
    ``expired_token`` can arrive as 4xx with the same shape, and the caller
    wants the body either way. Transport-level failures (DNS, TLS, refused)
    still raise ``urllib.error.URLError``/``OSError``; the sync path catches
    those and the device flow lets them out, because a device flow that cannot
    reach GitHub has nothing to show the operator but the failure.
    """
    body = None if data is None else json.dumps(data).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _parse(response.read())
    except urllib.error.HTTPError as e:
        # A 4xx still carries the body that names the error; see above.
        return e.code, _parse(e.read())


def _parse(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _transport(transport):
    return http_json if transport is None else transport


# ── where the credentials live (NOT the config dir) ─────────────────────────

def state_dir() -> Path:
    """``~/.local/state/reflex``, or :data:`STATE_DIR_ENV`.

    Deliberately NOT under :func:`reflex.utils.paths.config_dir` -- see the
    module docstring. The XDG state directory is the right home for "data the
    program needs across runs that the user would not hand-edit and would not
    want restored from someone else's backup", which is precisely an OAuth
    token and a gist id.
    """
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "reflex"


def token_path() -> Path:
    return state_dir() / TOKEN_NAME


def gist_id_path() -> Path:
    return state_dir() / GIST_ID_NAME


def enabled_path() -> Path:
    """The toggle. Its EXISTENCE is the setting.

    Not a YAML file under the config dir like every other preference, and the
    difference is deliberate: a preference there is exported by the bundle and
    applied by a restore, so importing another machine's bundle would silently
    switch this machine's cloud sync on. The toggle lives beside the token it
    governs and travels with neither.
    """
    return state_dir() / ENABLED_NAME


def is_configured() -> bool:
    """False in a fork with no OAuth app of its own."""
    return bool(GIST_CLIENT_ID)


def is_enabled() -> bool:
    return is_configured() and enabled_path().exists()


def set_enabled(enabled: bool) -> None:
    path = enabled_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def save_token(token: str) -> Path:
    """Write ``token`` 0600, never briefly wider.

    ``os.open`` with the mode rather than ``open()`` + ``chmod``: the latter
    creates the file at the process umask first, so there is a window in which
    the token is world-readable on a card whose umask is 022. ``O_TRUNC`` does
    not reset the mode of an existing file, so an already-0600 file stays 0600
    and an already-wrong one is repaired by the explicit ``chmod``.
    """
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, TOKEN_MODE)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(path, TOKEN_MODE)
    return path


def load_token() -> str | None:
    """The stored token, or ``None`` when there is none / it is unreadable."""
    try:
        text = token_path().read_text().strip()
    except OSError:
        return None
    return text or None


def forget_token() -> None:
    """Drop the local copy. Does NOT revoke the grant -- only the operator can
    do that, at :data:`REVOKE_URL`, which is why that URL is on the screen."""
    token_path().unlink(missing_ok=True)


def _read_gist_id() -> str | None:
    try:
        text = gist_id_path().read_text().strip()
    except OSError:
        return None
    return text or None


def _write_gist_id(gist_id: str) -> None:
    path = gist_id_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gist_id)


# ── the device flow ─────────────────────────────────────────────────────────

@dataclass
class DeviceCode:
    """What GitHub hands back at the start of the flow.

    :param user_code: the eight characters the operator types. Shown as LARGE
        TEXT on the Backup screen.
    :param verification_uri: where they type it. Also shown as a QR code
        beside the user code (segno, added 2026-09-17), so the phone gets there
        with one scan; GitHub gives no code-prefilled URL, so the code itself
        is still typed.
    :param interval: seconds GitHub asks us to wait between polls. Obeyed, and
        raised on ``slow_down``.
    """
    device_code: str
    user_code: str
    verification_uri: str
    interval: int = 5
    expires_in: int = 900


def start_device_flow(*, transport=None) -> DeviceCode:
    """Ask GitHub for a user code. Raises :class:`NotConfigured` -- WITHOUT
    making a request -- when there is no client id."""
    if not is_configured():
        raise NotConfigured()
    status, body = _transport(transport)(
        DEVICE_CODE_URL, method="POST",
        data={"client_id": GIST_CLIENT_ID, "scope": SCOPE})
    if body.get("error") or "device_code" not in body:
        raise GistSyncError(_error_message(body, status))
    return DeviceCode(
        device_code=body["device_code"],
        user_code=body.get("user_code", ""),
        verification_uri=body.get("verification_uri", "https://github.com/login/device"),
        interval=int(body.get("interval", 5) or 5),
        expires_in=int(body.get("expires_in", 900) or 900),
    )


#: Operator-facing text for each terminal device-flow error. GitHub's own
#: strings are for developers ("The device code has expired"); these say what
#: to do next.
FLOW_ERRORS = {
    "expired_token": ("The code expired before it was entered. "
                      "Turn the toggle on again for a fresh code."),
    "access_denied": ("Authorization was denied on GitHub. "
                      "Gist sync stays off."),
    "incorrect_client_credentials": (
        "GitHub rejected this build's client id. Gist sync stays off."),
    "device_flow_disabled": (
        "This build's OAuth app does not have the device flow enabled."),
}


def _error_message(body: dict, status: int) -> str:
    code = body.get("error")
    if code in FLOW_ERRORS:
        return FLOW_ERRORS[code]
    if code:
        return f"GitHub refused the request ({code})."
    return f"GitHub returned an unexpected response (HTTP {status})."


class DeviceFlow:
    """One in-progress device authorization, polled a STEP AT A TIME.

    :meth:`poll` does exactly one request and returns, so the caller owns the
    waiting. That is what lets the Setup screen drive the flow off a Kivy
    ``Clock`` tick instead of sleeping on the UI thread, and it is what lets a
    test drive twenty polls in no time at all.

    ``interval`` is public and mutable because ``slow_down`` MOVES it: GitHub's
    answer to polling too fast is not an error to show anybody, it is an
    instruction to wait longer, and a client that ignored it would be
    rate-limited out of the flow it just started.
    """

    def __init__(self, code: DeviceCode, *, transport=None):
        self.code = code
        self.interval = code.interval
        self.transport = transport
        self.done = False

    def poll(self) -> str | None:
        """One poll. Returns the access token, or ``None`` for "still waiting".

        Raises :class:`GistSyncError` on a terminal answer (``expired_token``,
        ``access_denied``), whose message is the operator-facing one.
        """
        status, body = _transport(self.transport)(
            ACCESS_TOKEN_URL, method="POST", data={
                "client_id": GIST_CLIENT_ID,
                "device_code": self.code.device_code,
                "grant_type": DEVICE_GRANT_TYPE,
            })
        error = body.get("error")
        if error == "authorization_pending":
            return None
        if error == "slow_down":
            # Obey whichever is larger: GitHub's own suggestion or the
            # documented +5. Taking the smaller would risk a second slow_down.
            suggested = int(body.get("interval", 0) or 0)
            self.interval = max(self.interval + SLOW_DOWN_BUMP, suggested)
            return None
        if error or not body.get("access_token"):
            self.done = True
            raise GistSyncError(_error_message(body, status))
        self.done = True
        return body["access_token"]

    def run(self, *, sleep, max_polls: int = 500) -> str:
        """Poll until it resolves, sleeping via the injected ``sleep``.

        The blocking form, used by the Setup screen's worker thread and by the
        tests. ``sleep`` is injected rather than imported so a test never
        actually waits; ``max_polls`` is a backstop so a fake that answers
        ``authorization_pending`` forever cannot hang a suite.
        """
        for _ in range(max_polls):
            token = self.poll()
            if token:
                return token
            sleep(self.interval)
        raise GistSyncError("Timed out waiting for GitHub authorization.")


def authorize(*, transport=None, sleep, on_code=None) -> str:
    """Run the whole flow and return the token, saving it 0600.

    :param on_code: called with the :class:`DeviceCode` as soon as it exists,
        which is what puts the user code on screen BEFORE the polling starts.
    """
    code = start_device_flow(transport=transport)
    if on_code is not None:
        on_code(code)
    token = DeviceFlow(code, transport=transport).run(sleep=sleep)
    save_token(token)
    return token


# ── the sync itself ─────────────────────────────────────────────────────────

def description_for(machine_id: str) -> str:
    return f"{DESCRIPTION_PREFIX}{machine_id}"


def _bundle_text(doc: dict) -> str:
    """The SAME single YAML document the USB export writes. Same dump options
    (``sort_keys=False``) so ``meta`` stays first and a gist revision diff is
    readable."""
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def push_bundle(doc: dict | None = None, *, transport=None) -> str:
    """Create the gist once, then PATCH that same gist forever after.

    THE CREATE/PATCH SPLIT IS THE POINT. Posting a new gist per sync would
    leave the operator's account with one gist per calibration change and throw
    away the revision history that makes the sequence readable -- the gist's
    own history is the "what did this machine look like in July" answer, and it
    only exists if the id is stable. The id is remembered in
    :func:`gist_id_path`.

    ``public: false`` -- a SECRET gist. Secret is not private (anyone with the
    URL can read it), which is why the document is the commissioning bundle and
    nothing else; it never carries the token (see the module docstring and
    ``test_the_bundle_never_contains_the_token``).

    Raises on failure. :func:`sync_now` is the swallowing caller.
    """
    if not is_configured():
        raise NotConfigured()
    token = load_token()
    if not token:
        raise GistSyncError("Not connected to GitHub yet.")

    doc = commissioning_bundle.build() if doc is None else doc
    machine = (doc.get("meta") or {}).get("machine_id", "unknown")
    payload = {
        "description": description_for(machine),
        "files": {BUNDLE_FILENAME: {"content": _bundle_text(doc)}},
    }

    gist_id = _read_gist_id()
    if gist_id:
        status, body = _transport(transport)(
            f"{GISTS_URL}/{gist_id}", method="PATCH", data=payload, token=token)
        if status != 404:
            if status >= 400:
                raise GistSyncError(_error_message(body, status))
            return gist_id
        # The operator deleted it in the browser. Fall through and make a new
        # one rather than failing forever on a dead id.
        log.warning(f"gist sync: gist {gist_id} is gone; creating a new one")

    payload["public"] = False
    status, body = _transport(transport)(
        GISTS_URL, method="POST", data=payload, token=token)
    if status >= 400 or not body.get("id"):
        raise GistSyncError(_error_message(body, status))
    _write_gist_id(body["id"])
    return body["id"]


def sync_now(doc: dict | None = None, *, transport=None) -> str | None:
    """:func:`push_bundle`, with every failure logged and swallowed.

    Returns the gist id, or ``None`` if it did not happen. THIS is what the
    ledger hook and the "Sync now" button call: a sync that raised would take
    out a config save (the ledger's caller) or the UI thread, and neither is
    worth a cloud copy. The retry is the next commissioning change -- nothing
    is marked done, so the next call tries the same work with fresher input.
    """
    try:
        return push_bundle(doc, transport=transport)
    except Exception as e:
        log.error(f"gist sync failed (will retry at the next change): {e}")
        return None


def sync_if_enabled(doc: dict | None = None, *, transport=None) -> str | None:
    """:func:`sync_now`, but only when the operator turned it on."""
    if not is_enabled():
        return None
    return sync_now(doc, transport=transport)


# ── restore ─────────────────────────────────────────────────────────────────

@dataclass
class GistRef:
    """One candidate in the restore list."""
    id: str
    description: str
    machine_id: str
    updated_at: str = ""


def list_machine_gists(machine_id: str | None = None, *, transport=None) -> list[GistRef]:
    """The operator's gists that this feature wrote, newest first.

    :param machine_id: keep only gists for THIS machine when given. The restore
        screen passes ``None`` on purpose -- restoring onto a replacement card
        means reading the DEAD machine's gist, and the dead machine's
        ``/etc/machine-id`` is exactly what the new card does not have.
    """
    if not is_configured():
        raise NotConfigured()
    token = load_token()
    if not token:
        raise GistSyncError("Not connected to GitHub yet.")
    status, body = _transport(transport)(GISTS_URL, token=token)
    if status >= 400:
        raise GistSyncError(_error_message(body if isinstance(body, dict) else {}, status))
    items = body if isinstance(body, list) else body.get("items", [])
    refs = []
    for item in items:
        description = (item or {}).get("description") or ""
        if not description.startswith(DESCRIPTION_PREFIX):
            continue
        found = description[len(DESCRIPTION_PREFIX):].strip()
        if machine_id is not None and found != machine_id:
            continue
        refs.append(GistRef(id=item.get("id", ""), description=description,
                            machine_id=found, updated_at=item.get("updated_at", "")))
    refs.sort(key=lambda r: r.updated_at, reverse=True)
    return refs


def fetch_bundle(gist_id: str, *, transport=None) -> dict:
    """Pull one gist and parse its bundle file into the document.

    Hands back the SAME shape ``yaml.safe_load`` of a USB export gives, which
    is what lets the Setup screen feed it to the existing confirm dialog and
    ``commissioning_bundle.apply`` path instead of growing a second importer.
    """
    if not is_configured():
        raise NotConfigured()
    token = load_token()
    if not token:
        raise GistSyncError("Not connected to GitHub yet.")
    status, body = _transport(transport)(f"{GISTS_URL}/{gist_id}", token=token)
    if status >= 400:
        raise GistSyncError(_error_message(body, status))
    files = body.get("files") or {}
    entry = files.get(BUNDLE_FILENAME)
    if entry is None and files:
        # Tolerate a renamed file: there is only ever one in our own gists, and
        # refusing on the name would strand a bundle the operator can see.
        entry = next(iter(files.values()))
    if not entry or "content" not in entry:
        raise GistSyncError(f"Gist {gist_id} has no commissioning bundle in it.")
    doc = yaml.safe_load(entry["content"])
    if not isinstance(doc, dict):
        raise GistSyncError(f"Gist {gist_id} does not contain a bundle document.")
    return doc


# ── the ledger hook ─────────────────────────────────────────────────────────

_sync_lock = threading.Lock()
_sync_again = False


def _start_worker(fn) -> None:
    """Seam: tests replace this to run the worker inline."""
    threading.Thread(target=fn, name="gist-sync", daemon=True).start()


def _on_commissioning_change(count: int) -> None:
    """Registered with :func:`reflex.utils.commissioning_ledger.on_change`.

    Runs AFTER the ledger line and its snapshot are on disk, outside the
    ledger's write path, and swallows everything (``sync_if_enabled`` ->
    ``sync_now``). ``count`` is unused beyond "something moved".

    OFF THE CALLING THREAD (2026-09-19). The ledger notifies from inside a
    config save, which is the Kivy thread; a sync is an HTTPS round trip, so
    running it inline froze the screen for as long as GitHub took -- or for
    the whole timeout on a shop network with no route out. Changes that land
    while a sync is running are coalesced into one more sync afterwards.
    """
    global _sync_again
    if not _sync_lock.acquire(blocking=False):
        _sync_again = True
        return
    _start_worker(_sync_worker)


def _sync_worker() -> None:
    global _sync_again
    try:
        while True:
            _sync_again = False
            sync_if_enabled()
            if not _sync_again:
                break
    finally:
        _sync_lock.release()
    if _sync_again:                 # arrived between the check and the release
        _on_commissioning_change(0)


def install_ledger_hook() -> None:
    """Make a recorded commissioning change trigger a sync. Idempotent.

    Called from the Setup screen when the toggle goes on, and at app start
    by :func:`install_ledger_hook_if_enabled` -- so a machine that never opted
    in never registers anything at all.
    """
    from reflex.utils import commissioning_ledger
    commissioning_ledger.on_change(_on_commissioning_change)


def install_ledger_hook_if_enabled() -> bool:
    """At app start: re-arm the hook when sync is on. Never raises.

    THE HOOK LIVES IN MEMORY; THE TOGGLE LIVES ON DISK. Until 2026-09-19 only
    the Backup screen's toggle installed it, so the first UI restart after
    turning sync on silently stopped every automatic sync while the screen
    still showed it ON. Measured on the lathe: sync enabled and a token
    present, the ledger growing on 09-19, and the gist last written on
    09-17 21:08 -- the moment the toggle was flipped.
    """
    try:
        if is_configured() and is_enabled():
            install_ledger_hook()
            return True
    except Exception as e:  # a lathe must boot whatever the gist state is
        log.error(f"gist sync: could not re-arm the ledger hook at start ({e})")
    return False


def bundle_contains_token(text: str) -> bool:
    """True if ``text`` carries the stored token. A guard for the test that
    asserts a bundle never does; not used in the app's own paths."""
    token = load_token()
    return bool(token) and token in text


def token_is_outside_config_dir() -> bool:
    """True when :func:`token_path` is NOT under :func:`config_dir`.

    An assertion the test suite makes, expressed here so the reason lives next
    to the path it constrains: the config dir is exported by the USB bundle,
    snapshotted by the ledger and pulled by the nightly backup.
    """
    try:
        token_path().resolve().relative_to(config_dir().resolve())
    except ValueError:
        return True
    return False
