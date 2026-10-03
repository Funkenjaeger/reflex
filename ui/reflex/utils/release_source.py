"""An optional second release source: a private forge's integration builds.

WHY IT EXISTS. Public releases come from GitHub (:mod:`reflex.utils.updater`).
A machine that is also a development bench can be pointed at a private forge
that publishes integration builds (``vX.Y.Z-alpha.N``) in the same shape -- a
release with a tag and a ``reflex-app-*.bin`` asset -- so a build can be
bench-tested on the real machine without publishing it.

NOTHING HERE NAMES A FORGE. The source exists only when a config file is
present on the machine, written by whoever provisions it (elspi's site hooks).
Without that file :func:`load_home_source` returns None and the updater, the
Update screen and every fetch behave exactly as they do with no such feature.

THE FILE (:data:`HOME_SOURCE_PATH`), parsed as data and never sourced, one
``KEY=value`` per line (blank lines and ``#`` comments allowed):

    RELEASES_URL=https://<forge>/api/v1/repos/<owner>/<repo>/releases
    GIT_URL=https://<forge>/<owner>/<repo>.git
    USER=<read-only account>
    TOKEN=<that account's read-only token>

All four are required, both URLs must be HTTPS, and any other key refuses the
file: a typo must not be silently ignored. The file holds a credential, so it
is refused when other users can read it; the service user needs read access,
so ``root:<service group> 0640`` or ``<service user> 0600`` both work.

A refused or absent file is never an error the operator sees: it is logged and
the screen simply offers public releases, as on any machine.
"""
from __future__ import annotations

import base64
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from kivy.logger import Logger

log = Logger.getChild(__name__)

HOME_SOURCE_PATH = Path("/etc/reflex/home-release-source")

_REQUIRED = ("RELEASES_URL", "GIT_URL", "USER", "TOKEN")


class HomeSourceInvalid(Exception):
    """The config file exists but cannot be used. The message never carries
    the token."""


@dataclass(frozen=True)
class HomeSource:
    releases_url: str
    git_url: str
    user: str
    token: str = field(repr=False)

    def __repr__(self) -> str:      # never the token, even in a traceback
        return f"HomeSource(releases_url={self.releases_url!r}, git_url={self.git_url!r}, user={self.user!r})"

    @property
    def origin(self) -> str:
        """``scheme://host[:port]`` of the release API. Credentials are only
        ever sent to this origin: a release whose asset URL points anywhere
        else is downloaded without them (see :meth:`auth_headers_for`)."""
        parts = urlsplit(self.releases_url)
        return f"{parts.scheme}://{parts.netloc}"

    def _basic(self) -> str:
        raw = f"{self.user}:{self.token}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def auth_headers_for(self, url: str) -> dict:
        """The Authorization header for ``url``, or none at all when ``url``
        is not on this source's origin."""
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self.origin:
            return {}
        return {"Authorization": self._basic()}

    def git_env(self) -> dict:
        """Environment for a ``git fetch`` from :attr:`git_url`. The header is
        passed through ``GIT_CONFIG_*`` and scoped to that URL, so the token is
        never in the command line, the URL, or any git config file."""
        return {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{self.git_url}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: {self._basic()}",
        }


def parse_home_source(text: str) -> HomeSource:
    values = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise HomeSourceInvalid(f"line {n} is not KEY=value")
        if key not in _REQUIRED:
            raise HomeSourceInvalid(f"line {n}: unknown key {key!r}")
        if key in values:
            raise HomeSourceInvalid(f"line {n}: {key} given twice")
        values[key] = value
    missing = [k for k in _REQUIRED if not values.get(k)]
    if missing:
        raise HomeSourceInvalid(f"missing {', '.join(missing)}")
    for key in ("RELEASES_URL", "GIT_URL"):
        if urlsplit(values[key]).scheme != "https" or not urlsplit(values[key]).netloc:
            raise HomeSourceInvalid(f"{key} must be an https:// URL")
    return HomeSource(releases_url=values["RELEASES_URL"], git_url=values["GIT_URL"],
                      user=values["USER"], token=values["TOKEN"])


def load_home_source(path: Path | None = None) -> HomeSource | None:
    """The configured home source, or None (absent, unreadable or refused).
    Never raises."""
    path = Path(path or HOME_SOURCE_PATH)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning(f"home release source: cannot stat {path} ({e.strerror}); not offered")
        return None
    if not stat.S_ISREG(st.st_mode):
        log.warning(f"home release source: {path} is not a regular file; not offered")
        return None
    if st.st_mode & stat.S_IROTH:
        log.warning(f"home release source: {path} is readable by other users and holds "
                    f"a credential; refused, not offered (chmod o-r)")
        return None
    try:
        text = path.read_text()
    except OSError as e:
        log.warning(f"home release source: cannot read {path} ({e.strerror}); not offered")
        return None
    try:
        return parse_home_source(text)
    except HomeSourceInvalid as e:
        log.warning(f"home release source: {path} refused: {e}; not offered")
        return None
