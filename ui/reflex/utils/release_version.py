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

This module is the one place that knows the mapping: :func:`tag_for` turns a
package version into the tag spelling for display, and :func:`same_release`
compares any two spellings canonically.
"""
from __future__ import annotations

import importlib.metadata
import re

# Package version as uv writes it: release segment, optional a/b/rc pre-release.
_PACKAGE = re.compile(r"^(?P<base>\d+(?:\.\d+)*)(?:(?P<pre>a|b|rc)(?P<n>\d+))?$")
# The tag's pre-release word for each normalized letter (PEP 440 normalizes
# alpha -> a, beta -> b, and rc stays rc). Only rc has ever been cut.
_TAG_PRE = {"a": "alpha", "b": "beta", "rc": "rc"}
# Canonicalizing a tag: strip v, map the spelled-out words back to the letter.
_CANON_WORDS = (("alpha", "a"), ("beta", "b"), ("preview", "rc"), ("pre", "rc"), ("c", "rc"))


def tag_for(version: str) -> str:
    """``1.2.0rc7`` -> ``v1.2.0-rc.7``; ``1.2.0`` -> ``v1.2.0``. Anything not
    in that shape (a dev or local build) is shown as ``v`` + itself rather
    than guessed at."""
    m = _PACKAGE.match(version)
    if not m:
        return "v" + version
    tag = "v" + m["base"]
    if m["pre"]:
        tag += f"-{_TAG_PRE[m['pre']]}.{m['n']}"
    return tag


def installed_tag() -> str:
    """The installed reflex package, spelled as its release tag."""
    return tag_for(importlib.metadata.version("reflex"))


def _canonical(spelling: str) -> str:
    s = spelling.strip().lower()
    if s.startswith("v"):
        s = s[1:]
    m = re.match(r"^(\d+(?:\.\d+)*)(?:[-.]?([a-z]+)[-.]?(\d+))?$", s)
    if not m:
        return s
    base, word, n = m.groups()
    if not word:
        return base
    for long, short in _CANON_WORDS:
        if word == long:
            word = short
            break
    return f"{base}{word}{int(n)}"


def same_release(a: str, b: str) -> bool:
    """True when two spellings name the same release: ``v1.2.0-rc.7``,
    ``1.2.0rc7`` and ``v1.2.0rc7`` are all one release."""
    return bool(a) and bool(b) and _canonical(a) == _canonical(b)
