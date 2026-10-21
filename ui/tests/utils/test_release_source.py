"""The optional home release source: its config file, and where its credential
may go. See reflex/utils/release_source.py.

What matters here is refusal and containment: a file that is malformed or
readable by other users must produce NO source (the screen then behaves as on
any machine), and the credential must reach only the source's own origin and
never appear in a repr, a URL or a git config key."""
import os

import pytest

from reflex.utils.release_source import (HomeSourceInvalid, load_home_source,
                                         parse_home_source)

TOKEN = "0123456789abcdef0123456789abcdef01234567"
GOOD = (
    "# provisioned by a site hook\n"
    "RELEASES_URL=https://forge.example/api/v1/repos/acme/reflex/releases\n"
    "GIT_URL=https://forge.example/acme/reflex.git\n"
    "\n"
    "USER=bench\n"
    f"TOKEN={TOKEN}\n"
)


def _write(tmp_path, text, mode=0o600):
    p = tmp_path / "home-release-source"
    p.write_text(text)
    os.chmod(p, mode)
    return p


def test_a_good_file_parses():
    s = parse_home_source(GOOD)
    assert s.releases_url == "https://forge.example/api/v1/repos/acme/reflex/releases"
    assert s.git_url == "https://forge.example/acme/reflex.git"
    assert s.user == "bench" and s.token == TOKEN
    assert s.origin == "https://forge.example"


@pytest.mark.parametrize("text, why", [
    (GOOD.replace("USER=bench\n", ""), "missing USER"),
    (GOOD + "TOKN=x\n", "unknown key"),
    (GOOD + "USER=other\n", "given twice"),
    (GOOD.replace("https://forge.example/acme", "http://forge.example/acme"), "https"),
    (GOOD + "not a pair\n", "KEY=value"),
    (GOOD.replace(f"TOKEN={TOKEN}", "TOKEN="), "missing TOKEN"),
])
def test_a_bad_file_is_refused(text, why):
    with pytest.raises(HomeSourceInvalid, match=why):
        parse_home_source(text)


def test_no_message_or_repr_carries_the_token():
    s = parse_home_source(GOOD)
    assert TOKEN not in repr(s) and TOKEN not in str(s)
    with pytest.raises(HomeSourceInvalid) as e:
        parse_home_source(GOOD + "EXTRA=1\n")
    assert TOKEN not in str(e.value)


def test_absent_file_is_no_source(tmp_path):
    assert load_home_source(tmp_path / "nope") is None


def test_a_good_private_file_loads(tmp_path):
    assert load_home_source(_write(tmp_path, GOOD, 0o640)) is not None


def test_a_world_readable_file_is_refused(tmp_path):
    """It holds a credential: readable by every user on the machine is a
    leak, so it is not used at all."""
    assert load_home_source(_write(tmp_path, GOOD, 0o644)) is None


def test_a_malformed_file_is_no_source_not_an_error(tmp_path):
    assert load_home_source(_write(tmp_path, "garbage\n")) is None


def test_a_directory_is_no_source(tmp_path):
    d = tmp_path / "dir"
    d.mkdir(mode=0o700)
    assert load_home_source(d) is None


def test_credentials_go_only_to_the_sources_origin():
    s = parse_home_source(GOOD)
    assert "Authorization" in s.auth_headers_for(
        "https://forge.example/acme/reflex/releases/download/v1.3.0-alpha.1/reflex-app-1.3.0-alpha.1.bin")
    for elsewhere in ("https://github.com/x/y/releases/download/a.bin",
                      "http://forge.example/acme/reflex.git",          # downgraded scheme
                      "https://forge.example:8443/a.bin",              # another port
                      "https://forge.example.evil/a.bin"):
        assert s.auth_headers_for(elsewhere) == {}, elsewhere


def test_the_git_credential_is_scoped_to_the_git_url_and_not_in_its_key():
    s = parse_home_source(GOOD)
    env = s.git_env()
    assert env["GIT_CONFIG_KEY_0"] == "http.https://forge.example/acme/reflex.git.extraHeader"
    assert TOKEN not in env["GIT_CONFIG_KEY_0"]
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert env["GIT_TERMINAL_PROMPT"] == "0"
