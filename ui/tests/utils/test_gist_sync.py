"""The opt-in gist sync: device flow, token storage, create-then-PATCH, restore.

NOTHING HERE TOUCHES THE NETWORK. ``gist_sync`` funnels every request through
one function, :func:`gist_sync.http_json`, and every entry point takes a
``transport=`` that defaults to it. Each test passes a :class:`FakeHttp`
instead, so the whole device flow and both gist paths are exercised without a
socket. ``FakeHttp`` also RECORDS its calls, which is how "the first sync
creates a gist, later syncs PATCH the same id" is asserted as a fact about the
requests rather than about a return value.

The state directory is redirected per-test via ``REFLEX_STATE_DIR`` -- these
tests write a token file and check its MODE, which must never happen in a
developer's real ``~/.local/state``.
"""
import os
import stat

import pytest
import yaml

from reflex.utils import commissioning_bundle, gist_sync


# ── the fake HTTP layer (the one seam) ──────────────────────────────────────

class FakeHttp:
    """Stands in for ``gist_sync.http_json``.

    ``responses`` is a list of ``(status, body)`` handed out in order; the last
    one repeats, so a test that polls an unknown number of times does not have
    to pad it. Every call is appended to ``calls`` as the kwargs it was made
    with, plus the url.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, method="GET", data=None, token=None, timeout=30.0):
        self.calls.append({"url": url, "method": method, "data": data,
                           "token": token})
        if not self.responses:
            raise AssertionError(f"FakeHttp ran out of responses at {url}")
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class ExplodingHttp:
    """A transport that fails the way a dead network does."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        raise OSError("Network is unreachable")


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Keep the token, gist id and enabled marker inside tmp_path."""
    target = tmp_path / "state"
    monkeypatch.setenv(gist_sync.STATE_DIR_ENV, str(target))
    return target


@pytest.fixture
def token(state_dir):
    gist_sync.save_token("gho_fake-token-for-tests")
    return "gho_fake-token-for-tests"


DEVICE_CODE_BODY = {
    "device_code": "dc-abc",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://github.com/login/device",
    "interval": 5,
    "expires_in": 900,
}


def _no_sleep(_seconds):
    pass


# ── device flow ─────────────────────────────────────────────────────────────

def test_device_flow_happy_path_returns_the_token_and_asks_only_for_gist():
    http = FakeHttp((200, DEVICE_CODE_BODY), (200, {"access_token": "gho_x"}))

    code = gist_sync.start_device_flow(transport=http)
    flow = gist_sync.DeviceFlow(code, transport=http)

    assert code.user_code == "WDJB-MJHT"
    assert code.verification_uri == "https://github.com/login/device"
    assert flow.run(sleep=_no_sleep) == "gho_x"

    start = http.calls[0]
    assert start["url"] == gist_sync.DEVICE_CODE_URL
    assert start["data"]["scope"] == "gist", "only the gist scope, ever"
    assert start["data"]["client_id"] == gist_sync.GIST_CLIENT_ID
    assert "client_secret" not in start["data"], "device flow has no secret"


def test_authorization_pending_then_success(monkeypatch):
    http = FakeHttp(
        (200, DEVICE_CODE_BODY),
        (200, {"error": "authorization_pending"}),
        (200, {"error": "authorization_pending"}),
        (200, {"access_token": "gho_y"}),
    )
    code = gist_sync.start_device_flow(transport=http)

    assert gist_sync.DeviceFlow(code, transport=http).run(sleep=_no_sleep) == "gho_y"
    # start + three polls
    assert len(http.calls) == 4


def test_slow_down_raises_the_interval():
    http = FakeHttp(
        (200, {"error": "slow_down"}),
        (200, {"access_token": "gho_z"}),
    )
    flow = gist_sync.DeviceFlow(
        gist_sync.DeviceCode(device_code="dc", user_code="U", interval=5,
                             verification_uri="https://example.invalid"),
        transport=http)

    assert flow.poll() is None, "slow_down is not a result, it is an instruction"
    assert flow.interval == 5 + gist_sync.SLOW_DOWN_BUMP
    assert flow.poll() == "gho_z"


def test_slow_down_obeys_a_larger_server_interval():
    http = FakeHttp((200, {"error": "slow_down", "interval": 42}))
    flow = gist_sync.DeviceFlow(
        gist_sync.DeviceCode(device_code="dc", user_code="U", interval=5,
                             verification_uri="https://example.invalid"),
        transport=http)

    flow.poll()

    assert flow.interval == 42


@pytest.mark.parametrize("error,fragment", [
    ("expired_token", "expired"),
    ("access_denied", "denied"),
])
def test_a_terminal_error_ends_the_flow_with_a_plain_message(error, fragment):
    http = FakeHttp((400, {"error": error}))
    flow = gist_sync.DeviceFlow(
        gist_sync.DeviceCode(device_code="dc", user_code="U",
                             verification_uri="https://example.invalid"),
        transport=http)

    with pytest.raises(gist_sync.GistSyncError) as excinfo:
        flow.poll()

    assert fragment in str(excinfo.value).lower()
    assert flow.done


def test_authorize_saves_the_token_and_reports_the_code(state_dir):
    http = FakeHttp((200, DEVICE_CODE_BODY), (200, {"access_token": "gho_saved"}))
    seen = []

    result = gist_sync.authorize(transport=http, sleep=_no_sleep,
                                 on_code=seen.append)

    assert result == "gho_saved"
    assert seen and seen[0].user_code == "WDJB-MJHT"
    assert gist_sync.load_token() == "gho_saved"


# ── the None-client-id guard ────────────────────────────────────────────────

@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(gist_sync, "GIST_CLIENT_ID", None)


def test_no_client_id_disables_the_feature_and_makes_no_request(unconfigured):
    http = FakeHttp((200, DEVICE_CODE_BODY))

    assert gist_sync.is_configured() is False
    assert gist_sync.is_enabled() is False
    with pytest.raises(gist_sync.NotConfigured):
        gist_sync.start_device_flow(transport=http)

    assert http.calls == [], "the guard must fire BEFORE any request"


def test_no_client_id_also_blocks_push_list_and_fetch(unconfigured, token):
    http = FakeHttp((200, {}))
    for call in (lambda: gist_sync.push_bundle({}, transport=http),
                 lambda: gist_sync.list_machine_gists(transport=http),
                 lambda: gist_sync.fetch_bundle("id", transport=http)):
        with pytest.raises(gist_sync.NotConfigured):
            call()
    assert http.calls == []


def test_the_client_id_is_the_registered_public_one():
    """Pinned so a typo cannot silently turn the feature into 'not configured'
    on the lathe. A device-flow client id is public by design."""
    assert gist_sync.GIST_CLIENT_ID == "Ov23libizt0tOMvxAojE"


# ── token storage: mode and location ────────────────────────────────────────

def test_token_file_is_created_0600(state_dir):
    path = gist_sync.save_token("gho_secret")

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"token file is {oct(mode)}, must be 0600"


def test_token_path_is_not_under_the_config_dir(tmp_path, monkeypatch):
    """THE POINT OF THE WHOLE STATE DIRECTORY. config_dir() is exported
    wholesale by the USB bundle, snapshotted by the ledger and pulled by the
    nightly backup; a token in there would be copied everywhere."""
    monkeypatch.delenv(gist_sync.STATE_DIR_ENV, raising=False)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "config"))

    from reflex.utils.paths import config_dir

    token = gist_sync.token_path().resolve()
    with pytest.raises(ValueError):
        token.relative_to(config_dir().resolve())
    assert gist_sync.token_is_outside_config_dir()
    assert "state" in str(gist_sync.token_path())


def test_the_bundle_never_contains_the_token(tmp_path, monkeypatch, state_dir):
    """build() walks config_dir(); the token is not there, so it cannot be in
    the document -- asserted rather than assumed, because the day somebody
    'simplifies' the token path into the config dir this is what says no."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "Axis-0.yaml").write_text("axis_name: C\nbacklash: 0.04\n")
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(config))
    gist_sync.save_token("gho_super_secret_value")

    doc = commissioning_bundle.build()
    text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)

    assert "gho_super_secret_value" not in text
    assert not gist_sync.bundle_contains_token(text)
    assert doc["Axis-0"]["axis_name"] == "C", "and it is still a real bundle"


def test_forget_token_removes_it(state_dir):
    gist_sync.save_token("gho_x")
    gist_sync.forget_token()

    assert gist_sync.load_token() is None
    gist_sync.forget_token()  # idempotent: must not raise when already gone


# ── the enabled marker ──────────────────────────────────────────────────────

def test_sync_is_off_until_it_is_turned_on(state_dir):
    http = FakeHttp((200, {"id": "g1"}))

    assert gist_sync.is_enabled() is False
    assert gist_sync.sync_if_enabled({}, transport=http) is None
    assert http.calls == [], "off means no request at all"

    gist_sync.set_enabled(True)
    assert gist_sync.is_enabled() is True
    gist_sync.set_enabled(False)
    assert gist_sync.is_enabled() is False


def test_the_enabled_marker_is_not_under_the_config_dir(tmp_path, monkeypatch):
    """Otherwise importing another machine's bundle would switch this machine's
    cloud sync on behind the operator's back."""
    monkeypatch.delenv(gist_sync.STATE_DIR_ENV, raising=False)
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "config"))

    from reflex.utils.paths import config_dir

    with pytest.raises(ValueError):
        gist_sync.enabled_path().resolve().relative_to(config_dir().resolve())


# ── create once, PATCH thereafter ───────────────────────────────────────────

DOC = {"meta": {"schema": 1, "machine_id": "5a2f", "hostname": "elspi"},
       "Axis-0": {"axis_name": "C"}}


def test_the_first_sync_creates_a_secret_gist(token):
    http = FakeHttp((201, {"id": "gist-1"}))

    assert gist_sync.push_bundle(DOC, transport=http) == "gist-1"

    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == gist_sync.GISTS_URL
    assert call["data"]["public"] is False, "a SECRET gist, never a public one"
    assert call["data"]["description"] == "reflex commissioning bundle: 5a2f"
    content = call["data"]["files"][gist_sync.BUNDLE_FILENAME]["content"]
    assert yaml.safe_load(content) == DOC, "the same single YAML document"
    assert content.startswith("meta:"), "meta first, as the USB export writes it"


def test_a_sync_with_no_doc_stamps_the_recorded_firmware(token, tmp_path, monkeypatch):
    """The path the Backup screen and the ledger hook both take (doc=None).
    The first real gist, 2026-09-17, carried fw: null because this path called
    build() back when build()'s default was None."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "Els-0.yaml").write_text("els_backlash_steps: 484\n")
    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(config))
    monkeypatch.setattr(commissioning_bundle, "recorded_fw_rev",
                        lambda: "43ac7c5 (v1.2.0-rc.3)")
    http = FakeHttp((201, {"id": "gist-1"}))

    assert gist_sync.push_bundle(transport=http) == "gist-1"

    content = http.calls[0]["data"]["files"][gist_sync.BUNDLE_FILENAME]["content"]
    doc = yaml.safe_load(content)
    assert doc["meta"]["fw"] == "43ac7c5 (v1.2.0-rc.3)"
    assert doc["Els-0"]["els_backlash_steps"] == 484


def test_later_syncs_patch_the_same_gist(token):
    create = FakeHttp((201, {"id": "gist-1"}))
    gist_sync.push_bundle(DOC, transport=create)

    patch = FakeHttp((200, {"id": "gist-1"}))
    assert gist_sync.push_bundle(DOC, transport=patch) == "gist-1"
    again = FakeHttp((200, {"id": "gist-1"}))
    gist_sync.push_bundle(DOC, transport=again)

    for http in (patch, again):
        assert len(http.calls) == 1
        assert http.calls[0]["method"] == "PATCH"
        assert http.calls[0]["url"] == f"{gist_sync.GISTS_URL}/gist-1"
        assert "public" not in http.calls[0]["data"]


def test_a_deleted_gist_is_recreated_rather_than_failing_forever(token):
    gist_sync.push_bundle(DOC, transport=FakeHttp((201, {"id": "gist-1"})))

    http = FakeHttp((404, {"message": "Not Found"}), (201, {"id": "gist-2"}))
    assert gist_sync.push_bundle(DOC, transport=http) == "gist-2"

    assert [c["method"] for c in http.calls] == ["PATCH", "POST"]
    assert gist_sync.push_bundle(DOC, transport=FakeHttp((200, {"id": "gist-2"})))


def test_the_request_carries_the_token_and_the_body_does_not(token):
    http = FakeHttp((201, {"id": "gist-1"}))

    gist_sync.push_bundle(DOC, transport=http)

    call = http.calls[0]
    assert call["token"] == token, "the token belongs in the Authorization header"
    assert token not in yaml.safe_dump(call["data"])


def test_push_without_a_token_refuses_before_any_request(state_dir):
    http = FakeHttp((201, {"id": "gist-1"}))

    with pytest.raises(gist_sync.GistSyncError):
        gist_sync.push_bundle(DOC, transport=http)

    assert http.calls == []


# ── failure is swallowed and retried at the next change ─────────────────────

def test_a_network_error_is_swallowed_logged_and_retried_next_change(token, caplog):
    dead = ExplodingHttp()

    assert gist_sync.sync_now(DOC, transport=dead) is None, "never raises"
    assert dead.calls == 1

    # Nothing was marked done, so the next commissioning change tries again --
    # and succeeds, creating the gist that the failed attempt did not.
    alive = FakeHttp((201, {"id": "gist-1"}))
    assert gist_sync.sync_now(DOC, transport=alive) == "gist-1"
    assert alive.calls[0]["method"] == "POST"


def test_a_refusal_from_github_is_also_swallowed(token):
    http = FakeHttp((422, {"message": "Validation Failed"}))

    assert gist_sync.sync_now(DOC, transport=http) is None


def test_the_ledger_hook_runs_outside_the_write_path_and_cannot_break_it(
        tmp_path, monkeypatch, state_dir):
    """A sync that throws must not cost a ledger line. The observer list is the
    seam; ``record`` calls it after the append and the snapshot."""
    from reflex.utils import commissioning_ledger

    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "config"))
    commissioning_ledger.clear_change_observers()

    def boom(_count):
        raise RuntimeError("sync exploded")

    commissioning_ledger.on_change(boom)
    try:
        written = commissioning_ledger.record(
            tmp_path / "Axis-0.yaml", None, {"axis_name": "C"}, "axis_name")
    finally:
        commissioning_ledger.clear_change_observers()

    assert written == 1
    assert commissioning_ledger.ledger_path().read_text().count("\n") == 1


def test_installing_the_hook_syncs_on_a_recorded_change(tmp_path, monkeypatch,
                                                        state_dir):
    from reflex.utils import commissioning_ledger

    monkeypatch.setenv("REFLEX_CONFIG_DIR", str(tmp_path / "config"))
    commissioning_ledger.clear_change_observers()
    calls = []
    monkeypatch.setattr(gist_sync, "sync_if_enabled",
                        lambda *a, **kw: calls.append(1))
    monkeypatch.setattr(gist_sync, "_start_worker", lambda fn: fn())

    gist_sync.install_ledger_hook()
    gist_sync.install_ledger_hook()  # idempotent
    try:
        commissioning_ledger.record(
            tmp_path / "Axis-0.yaml", None, {"axis_name": "C"}, "axis_name")
        # A save that changes no commissioning key must NOT sync.
        commissioning_ledger.record(
            tmp_path / "Axis-0.yaml", {"axis_name": "C"}, {"axis_name": "C"}, "")
    finally:
        commissioning_ledger.clear_change_observers()

    assert calls == [1], "one sync for the one real change, and none for the no-op"


def test_app_start_rearms_the_hook_when_sync_is_on(state_dir, monkeypatch):
    """MUTATION EVIDENCE. Found on the lathe 2026-09-19: sync ON, token
    present, ledger growing, gist untouched since the toggle was flipped --
    the hook lived only in the process that flipped it."""
    from reflex.utils import commissioning_ledger
    monkeypatch.setattr(gist_sync, "GIST_CLIENT_ID", "Ov23-test")
    commissioning_ledger.clear_change_observers()
    try:
        gist_sync.set_enabled(True)
        assert gist_sync.install_ledger_hook_if_enabled() is True
        assert gist_sync._on_commissioning_change in commissioning_ledger._change_observers
    finally:
        commissioning_ledger.clear_change_observers()


def test_app_start_registers_nothing_when_sync_is_off(state_dir, monkeypatch):
    from reflex.utils import commissioning_ledger
    monkeypatch.setattr(gist_sync, "GIST_CLIENT_ID", "Ov23-test")
    commissioning_ledger.clear_change_observers()
    gist_sync.set_enabled(False)
    assert gist_sync.install_ledger_hook_if_enabled() is False
    assert commissioning_ledger._change_observers == []


def test_app_build_calls_the_rearm():
    """The re-arm is only a fix if the app calls it at start."""
    import ast
    import inspect
    from reflex import app
    tree = ast.parse(inspect.getsource(app.MainApp.build).lstrip())
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "install_ledger_hook_if_enabled" in called


def test_the_hook_syncs_off_the_calling_thread(monkeypatch):
    """The ledger notifies from inside a config save on the Kivy thread; an
    HTTPS round trip there freezes the screen."""
    import threading
    done = threading.Event()
    seen = []

    def fake_sync(*a, **kw):
        seen.append(threading.get_ident())
        done.set()
    monkeypatch.setattr(gist_sync, "sync_if_enabled", fake_sync)
    gist_sync._on_commissioning_change(1)
    assert done.wait(5), "the sync never ran"
    assert seen and seen[0] != threading.get_ident()


def test_changes_during_a_sync_coalesce_into_one_more(monkeypatch):
    runs = []
    workers = []

    def fake_sync(*a, **kw):
        runs.append(1)
        if len(runs) == 1:              # two more changes land mid-sync
            gist_sync._on_commissioning_change(1)
            gist_sync._on_commissioning_change(1)
    monkeypatch.setattr(gist_sync, "sync_if_enabled", fake_sync)
    monkeypatch.setattr(gist_sync, "_start_worker", lambda fn: workers.append(fn))
    gist_sync._on_commissioning_change(1)
    [worker] = workers
    worker()
    assert runs == [1, 1], "one sync, then exactly one more for everything that landed during it"
    assert not gist_sync._sync_lock.locked()


# ── restore ─────────────────────────────────────────────────────────────────

GIST_LIST = [
    {"id": "g-old", "description": "reflex commissioning bundle: 5a2f",
     "updated_at": "2026-07-01T00:00:00Z"},
    {"id": "g-new", "description": "reflex commissioning bundle: 5a2f",
     "updated_at": "2026-09-01T00:00:00Z"},
    {"id": "g-other", "description": "reflex commissioning bundle: beef",
     "updated_at": "2026-08-01T00:00:00Z"},
    {"id": "g-unrelated", "description": "my shopping list",
     "updated_at": "2026-08-15T00:00:00Z"},
]


def test_list_machine_gists_matches_the_description_pattern_newest_first(token):
    http = FakeHttp((200, GIST_LIST))

    refs = gist_sync.list_machine_gists(transport=http)

    assert [r.id for r in refs] == ["g-new", "g-other", "g-old"]
    assert all(r.description.startswith(gist_sync.DESCRIPTION_PREFIX) for r in refs)
    assert refs[0].machine_id == "5a2f"


def test_list_machine_gists_can_filter_to_one_machine(token):
    http = FakeHttp((200, GIST_LIST))

    refs = gist_sync.list_machine_gists("beef", transport=http)

    assert [r.id for r in refs] == ["g-other"]


def test_fetch_bundle_parses_the_document(token):
    content = yaml.safe_dump(DOC, sort_keys=False)
    http = FakeHttp((200, {"files": {
        gist_sync.BUNDLE_FILENAME: {"content": content}}}))

    assert gist_sync.fetch_bundle("g-new", transport=http) == DOC
    assert http.calls[0]["url"] == f"{gist_sync.GISTS_URL}/g-new"


def test_fetch_bundle_refuses_a_gist_with_no_document(token):
    http = FakeHttp((200, {"files": {}}))

    with pytest.raises(gist_sync.GistSyncError):
        gist_sync.fetch_bundle("g-empty", transport=http)


def test_fetch_bundle_refuses_a_non_mapping_document(token):
    http = FakeHttp((200, {"files": {
        gist_sync.BUNDLE_FILENAME: {"content": "- just\n- a list\n"}}}))

    with pytest.raises(gist_sync.GistSyncError):
        gist_sync.fetch_bundle("g-bad", transport=http)


# ── the module never imports an http client ─────────────────────────────────

def test_only_the_stdlib_is_used_for_http():
    """No dependency was added for this feature; ``urllib.request`` is the
    whole HTTP stack. Guards against a later 'just use requests' edit."""
    import inspect

    source = inspect.getsource(gist_sync)
    for banned in ("import requests", "import httpx", "import aiohttp"):
        assert banned not in source
    assert "import urllib.request" in source
