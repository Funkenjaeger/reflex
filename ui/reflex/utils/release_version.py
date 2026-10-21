"""The installed release as its tag reads, and tag comparison that survives
the two spellings of one version.

TWO SPELLINGS. A release is tagged ``v1.2.0-rc.7`` (the repo's ``VERSION``
file, the release page, the Update screen's list of releases). The installed
package reports ``1.2.0rc7``: uv writes the PEP 440 normalized form into the
package metadata, which is where ``importlib.metadata.version`` reads it. Until
2026-09-25 the UI showed ``"v" + <package version>`` -- ``v1.2.0rc7`` -- in the
status bar and as "Currently installed release", and the Update screen compared
that string with the tags to decide whether the selected release was already
installed. For a final release the two spellings agree (``v1.2.0``); for every
pre-release they never did, so the installed pre-release always looked like a
different one. The release workflow already compares canonically for the same
reason (release.yml, "Versions are compared CANONICALLY").

INTEGRATION BUILDS ARE DEV RELEASES OF THE NEXT RC (2026-10-03). The tag
``v1.2.2-rc.2.dev.1`` is the package ``1.2.2rc2.dev1``: a build on the way to
rc.2. PEP 440 orders a dev release just before the release it leads to, so

    1.2.2rc1 < 1.2.2rc2.dev1 < 1.2.2rc2.dev2 < 1.2.2rc2 < 1.2.2

and integration builds and rcs can alternate on one version for as long as
needed. The earlier scheme, ``vX.Y.Z-alpha.N``, could not follow an rc: an
alpha sorts below every rc of its version. Those tags still parse and still
sort below ``rc.1``. NOTE that general SEMVER tooling reads
``1.2.2-rc.2.dev.1`` as AFTER ``1.2.2-rc.2`` (more pre-release fields rank
higher); reflex orders by PEP 440, here and in uv, and nothing it relies on
sorts its tags by semver.

This module is the one place that knows the mapping: :func:`tag_for` turns a
package version into the tag spelling for display, :func:`same_release`
compares any two spellings canonically, :func:`precedence_key` orders them,
and :func:`status_label` shortens tags for the status bar's narrow box.
"""
from __future__ import annotations

import importlib.metadata
import re

# Package version as uv writes it: release segment, optional a/b/rc
# pre-release, optional .devN.
_PACKAGE = re.compile(
    r"^(?P<base>\d+(?:\.\d+)*)(?:(?P<pre>a|b|rc)(?P<n>\d+))?(?:\.dev(?P<dev>\d+))?$")
# The tag's pre-release word for each normalized letter (PEP 440 normalizes
# alpha -> a, beta -> b, and rc stays rc).
_TAG_PRE = {"a": "alpha", "b": "beta", "rc": "rc"}
# Canonicalizing a tag: strip v, map the spelled-out words back to the letter.
_CANON_WORDS = (("alpha", "a"), ("beta", "b"), ("preview", "rc"), ("pre", "rc"), ("c", "rc"))


def tag_for(version: str) -> str:
    """``1.2.0rc7`` -> ``v1.2.0-rc.7``; ``1.2.2rc2.dev1`` ->
    ``v1.2.2-rc.2.dev.1``; ``1.2.0`` -> ``v1.2.0``. Anything not in those
    shapes (a local build) is shown as ``v`` + itself rather than guessed at."""
    m = _PACKAGE.match(version)
    if not m:
        return "v" + version
    tag = "v" + m["base"]
    if m["pre"]:
        tag += f"-{_TAG_PRE[m['pre']]}.{m['n']}"
    if m["dev"] is not None:
        tag += f".dev.{m['dev']}" if m["pre"] else f"-dev.{m['dev']}"
    return tag


# The status bar's short spellings. Its version box is 92 dp and shrinks the
# font to fit: v1.2.0-rc.7 renders at 14 px, and v1.3.0-alpha.12 would drop to
# 10 px. Alpha and beta are shown by their letter, which is never wider than
# the rc of the same numbers. A dev build keeps both numbers it needs to be
# told apart, ``v1.2.2-rc2.d1``: one character plus the dev number's digits
# wider than ``v1.2.2-rc.2``, the price of naming which rc it leads to.
_STATUS_PRE = (("-alpha.", "-a."), ("-beta.", "-b."))
_STATUS_DEV = re.compile(r"-rc\.(\d+)\.dev\.(\d+)$")


def status_label(tag: str) -> str:
    """The status bar's spelling of a tag: ``v1.3.0-alpha.12`` ->
    ``v1.3.0-a.12``, ``v1.3.0-beta.2`` -> ``v1.3.0-b.2``,
    ``v1.2.2-rc.2.dev.1`` -> ``v1.2.2-rc2.d1``. Finals and rcs are unchanged.
    Display only: everything that compares releases keeps the full tag."""
    m = _STATUS_DEV.search(tag)
    if m:
        return tag[:m.start()] + f"-rc{m[1]}.d{m[2]}"
    for long, short in _STATUS_PRE:
        if long in tag:
            return tag.replace(long, short, 1)
    return tag


def installed_tag() -> str:
    """The installed reflex package, spelled as its release tag."""
    return tag_for(importlib.metadata.version("reflex"))


_CANON_RE = re.compile(
    r"^(\d+(?:\.\d+)*)(?:[-.]?([a-z]+)[-.]?(\d+))?(?:[-.]?(?:dev|d)[-.]?(\d+))?$")


def _canonical(spelling: str) -> str:
    s = spelling.strip().lower()
    if s.startswith("v"):
        s = s[1:]
    m = _CANON_RE.match(s)
    if not m:
        return s
    base, word, n, dev = m.groups()
    if word in ("dev", "d") and dev is None:    # X.Y.Z.devN: no pre-release
        word, dev = None, n
    out = base
    if word:
        for long, short in _CANON_WORDS:
            if word == long:
                word = short
                break
        out += f"{word}{int(n)}"
    if dev is not None:
        out += f".dev{int(dev)}"
    return out


def same_release(a: str, b: str) -> bool:
    """True when two spellings name the same release: ``v1.2.0-rc.7``,
    ``1.2.0rc7`` and ``v1.2.0rc7`` are all one release, as are
    ``v1.2.2-rc.2.dev.1`` and ``1.2.2rc2.dev1``."""
    return bool(a) and bool(b) and _canonical(a) == _canonical(b)


_PRE_RANK = {"a": 0, "b": 1, "rc": 2}
_CANONICAL = re.compile(
    r"^(?P<base>\d+(?:\.\d+)*)(?:(?P<pre>a|b|rc)(?P<n>\d+))?(?:\.dev(?P<dev>\d+))?$")


def precedence_key(spelling: str) -> tuple:
    """Sort key in release order (PEP 440): ``1.3.0-alpha.4`` <
    ``1.3.0-beta.1`` < ``1.3.0-rc.1`` < ``1.3.0-rc.2.dev.1`` < ``1.3.0-rc.2``
    < ``1.3.0`` < ``1.3.1-rc.1.dev.1``. Used to merge the home source's
    integration builds into the public list. A spelling that is not a release
    shape sorts below every release, so it lands at the bottom of a
    newest-first list rather than on top."""
    m = _CANONICAL.match(_canonical(spelling))
    if not m:
        return (0,)
    base = tuple(int(p) for p in m["base"].split("."))
    if m["pre"]:
        pre = (_PRE_RANK[m["pre"]], int(m["n"]))
    elif m["dev"] is not None:
        pre = (-1, 0)       # X.Y.Z.devN precedes every pre-release of X.Y.Z
    else:
        pre = (3, 0)
    # A dev release sorts just BELOW the release it leads to.
    dev = (0, int(m["dev"])) if m["dev"] is not None else (1, 0)
    return (1, base, pre, dev)
