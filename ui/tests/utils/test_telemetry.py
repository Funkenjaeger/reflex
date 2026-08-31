"""Error reporting goes where it is told, or nowhere.

The defect this guards against is not hypothetical: the DSN used to be a
literal in app.py, inherited from the upstream author through the fork, with
reporting defaulting to ON. So the two properties that matter are that the
code carries NO destination of its own, and that missing configuration means
silence rather than a fallback.
"""
import pytest

from reflex.utils import telemetry


@pytest.fixture(autouse=True)
def _no_ambient_dsn(monkeypatch):
    """Never let the developer's own environment decide these tests."""
    monkeypatch.delenv(telemetry.DSN_ENV, raising=False)


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)


# --- the destination is configuration, never source -----------------------

def test_no_dsn_means_no_reporting():
    spy = _Spy()
    status = telemetry.configure(disabled=False, init=spy)
    assert spy.calls == []
    assert telemetry.DSN_ENV in status


def test_blank_dsn_is_unset_not_malformed(monkeypatch):
    """An empty export is how someone turns this off; it must mean off."""
    for blank in ("", "   ", "\t\n"):
        monkeypatch.setenv(telemetry.DSN_ENV, blank)
        spy = _Spy()
        telemetry.configure(disabled=False, init=spy)
        assert spy.calls == [], f"initialised on blank DSN {blank!r}"


def test_the_setting_wins_over_a_configured_dsn(monkeypatch):
    monkeypatch.setenv(telemetry.DSN_ENV, "https://k@example.ingest.sentry.io/42")
    spy = _Spy()
    status = telemetry.configure(disabled=True, init=spy)
    assert spy.calls == []
    assert "off" in status.lower()


def test_configured_dsn_is_used_verbatim(monkeypatch):
    dsn = "https://abc123@o999.ingest.us.sentry.io/4242"
    monkeypatch.setenv(telemetry.DSN_ENV, dsn)
    spy = _Spy()
    telemetry.configure(disabled=False, init=spy)
    assert len(spy.calls) == 1
    assert spy.calls[0]["dsn"] == dsn
    assert spy.calls[0]["send_default_pii"] is False


def test_source_carries_no_dsn_of_its_own():
    """The regression that motivated the module: a built-in destination."""
    import inspect
    src = inspect.getsource(telemetry)
    assert "ingest.sentry.io" not in src
    assert "ingest.us.sentry.io" not in src
    assert "@o4509625403506688" not in src


# --- a DSN embeds a key, so it must never reach a log ---------------------

def test_status_never_contains_the_key(monkeypatch):
    dsn = "https://SUPERSECRETKEY@o999.ingest.us.sentry.io/4242"
    monkeypatch.setenv(telemetry.DSN_ENV, dsn)
    status = telemetry.configure(disabled=False, init=_Spy())
    assert "SUPERSECRETKEY" not in status
    assert dsn not in status
    # ...but it still answers "where is this going".
    assert "o999.ingest.us.sentry.io" in status
    assert "4242" in status


@pytest.mark.parametrize("dsn,expected", [
    ("https://key@o1.ingest.us.sentry.io/42", "o1.ingest.us.sentry.io/42"),
    ("https://key@example.com/7", "example.com/7"),
    ("https://key@example.com/", "example.com/?"),
    ("not a url at all", "?/not a url at all"),
])
def test_describe_destination(dsn, expected):
    assert telemetry.describe_destination(dsn) == expected


def test_describe_destination_never_leaks_userinfo():
    got = telemetry.describe_destination("https://thekey@host.tld/9")
    assert "thekey" not in got
