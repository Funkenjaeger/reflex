#!/usr/bin/env python3
"""lock_version_parity.py -- refuse a release whose ui/uv.lock does not declare
the version the release is stamping.

THE DEFECT THIS EXISTS FOR, measured on integration 2026-09-11: VERSION said
1.1.0 while ui/uv.lock's own `reflex` entry still said 1.1.0rc1. Not a typo --
a structural consequence of the workflow's step order. `uv.lock` records the
root project's own version (it is an editable member of its own resolution), so
the lock only tells the truth after `uv lock` has re-read a stamped
ui/pyproject.toml. The release workflow stamped VERSION and ui/pyproject.toml,
COMMITTED that stamp, and only afterwards ran uv -- whose `uv sync` rewrote the
lock on the runner, after the commit, where nothing ever picked it up. So NO
TAGGED COMMIT CARRIED A LOCK MATCHING ITS OWN VERSION, in any release the
monorepo has cut. Anyone checking out v1.1.0 and running `uv sync` got a tree
that immediately disagreed with itself about what it was.

The fix is in .github/workflows/release.yml: uv is installed before the stamp,
the stamp step runs `uv lock` and includes ui/uv.lock in the stamp commit. This
script is the gate on that -- it reads the lock OUT OF THE STAMP COMMIT and
refuses the release if the declared version is not the one being stamped.

VERSIONS ARE COMPARED CANONICALLY, NOT AS STRINGS, and that is the whole reason
this is a script rather than a `grep -q` in the workflow. The repo spells a
pre-release `1.1.0-rc.1` (that is what the workflow's semver check accepts and
what the tag says); uv normalizes it to PEP 440 and writes `1.1.0rc1`. A string
comparison would fail every pre-release release -- the exact shape of guard that
gets deleted the first time it cries wolf -- while still passing 1.1.0 against
1.1.0rc1 if someone "fixed" it by stripping punctuation. Both spellings are
canonicalized and then compared.

Usage:
    lock_version_parity.py show  <uv.lock>
    lock_version_parity.py check <uv.lock> (--version <V> | --version-file <F>)
                                 [--pyproject <ui/pyproject.toml>] [--package reflex]
    lock_version_parity.py self-test

`check` exits 1 unless the lock holds EXACTLY ONE package entry named <package>
whose version canonicalizes equal to the expected version. Zero entries is a
failure and so are two: a check that cannot see the version it is vouching for,
or that has to pick between candidates, is not a check. A lock that does not
parse is a failure for the same reason. With --pyproject the package's own
`project.version` must agree as well, which is what keeps the three files
(VERSION, ui/pyproject.toml, ui/uv.lock) from drifting pairwise.

`self-test` runs `check` against synthetic fixtures -- ones that must pass and
one for each way the pair can be wrong, including the exact 1.1.0-vs-1.1.0rc1
shape measured above -- and exits 1 unless every case comes out the way it must.
The release workflow runs it immediately before the real check, so every release
re-proves the guard can still go red. This repo has shipped two guards that
could not fail (the diagnostic-probe grep, and the identity check before
2026-09-11); assume this one is next unless it demonstrates otherwise on every
run.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib

# --- PEP 440, the subset this repo can produce --------------------------------
# The release workflow accepts `^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$`, and
# uv writes back the PEP 440 normal form. The alternatives below cover both
# spellings plus the post/dev forms uv could emit, and anything outside them is
# an ERROR rather than a silent pass -- an unrecognized version string is
# exactly the case where a guard must not guess.
_VERSION_RE = re.compile(
    r"""^\s*v?
        (?P<release>[0-9]+(?:\.[0-9]+)*)
        (?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>[0-9]+)?)?
        (?:-(?P<post_implicit>[0-9]+)
          |[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n>[0-9]+)?)?
        (?:[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
        \s*$""",
    re.VERBOSE | re.IGNORECASE,
)

_PRE_CANON = {"alpha": "a", "a": "a", "beta": "b", "b": "b",
              "c": "rc", "pre": "rc", "preview": "rc", "rc": "rc"}


def canonical_version(raw: str) -> str:
    """PEP 440 normal form. `1.1.0-rc.1`, `1.1.0.rc1` and `1.1.0RC1` all become
    `1.1.0rc1`; `1.1.0` stays `1.1.0` and does NOT become equal to any of
    them."""
    if not isinstance(raw, str):
        raise ValueError(f"version is {type(raw).__name__}, not a string")
    m = _VERSION_RE.match(raw)
    if not m:
        raise ValueError(f"'{raw}' is not a version this guard can read")
    out = ".".join(str(int(p)) for p in m.group("release").split("."))
    if m.group("pre_l"):
        out += _PRE_CANON[m.group("pre_l").lower()] + str(int(m.group("pre_n") or 0))
    if m.group("post_implicit") is not None:
        out += ".post" + str(int(m.group("post_implicit")))
    elif m.group("post_l"):
        out += ".post" + str(int(m.group("post_n") or 0))
    if _has_dev(m):
        out += ".dev" + str(int(m.group("dev_n") or 0))
    return out


def _has_dev(m: re.Match) -> bool:
    """The dev group carries no letter of its own, so its presence is read off
    the matched span rather than a named group."""
    return bool(re.search(r"[-_.]?dev[-_.]?[0-9]*\s*$", m.group(0), re.IGNORECASE))


def canonical_name(raw: str) -> str:
    """PEP 503 normalized project name, so `reflex`, `Reflex` and `re_flex`
    cannot be three different packages to this guard."""
    return re.sub(r"[-_.]+", "-", raw).lower()


# --- reading the two files ----------------------------------------------------

def lock_entries(lock_text: str, package: str) -> list[dict]:
    """Every `[[package]]` table in a uv.lock named `package`. Raises on a lock
    that does not parse -- see the module docstring on why that is a failure and
    not a skip."""
    doc = tomllib.loads(lock_text)
    want = canonical_name(package)
    return [p for p in doc.get("package", [])
            if isinstance(p, dict) and canonical_name(str(p.get("name", ""))) == want]


def pyproject_version(text: str) -> str:
    doc = tomllib.loads(text)
    v = doc.get("project", {}).get("version")
    if v is None:
        raise ValueError("no [project] version field")
    return str(v)


def check(lock_text: str, expected: str, pyproject_text: str | None = None,
          package: str = "reflex") -> tuple[list[str], str]:
    """Return (problems, description). No problems means the lock declares
    `expected`. The description names what was read, for the log."""
    problems: list[str] = []
    try:
        want = canonical_version(expected)
    except ValueError as e:
        # Nothing downstream can mean anything if the expectation is unreadable.
        return ([f"the expected version is unusable: {e}"], "no expectation")

    try:
        entries = lock_entries(lock_text, package)
    except tomllib.TOMLDecodeError as e:
        return ([f"the lock does not parse as TOML ({e}) -- this check cannot see the "
                 "version it is meant to vouch for"], "unparseable lock")

    if not entries:
        return ([f"no '{package}' package entry in the lock -- this check cannot see the "
                 "version it is meant to vouch for"], "no entry")
    if len(entries) > 1:
        spellings = ", ".join(str(e.get("version")) for e in entries)
        return ([f"{len(entries)} '{package}' entries in the lock ({spellings}); refusing to "
                 "pick one"], "ambiguous")

    entry = entries[0]
    raw = entry.get("version")
    source = entry.get("source", {})
    src = ", ".join(f"{k}={v}" for k, v in source.items()) if isinstance(source, dict) else str(source)
    desc = f"lock declares {package} {raw!s} ({src or 'no source'})"
    try:
        got = canonical_version(str(raw))
    except ValueError as e:
        problems.append(f"the lock's version is unusable: {e}")
    else:
        if got != want:
            problems.append(f"lock declares {package} {raw!s} (canonically {got}), "
                            f"not the release version {expected} ({want})")

    if pyproject_text is not None:
        try:
            pv = pyproject_version(pyproject_text)
            pgot = canonical_version(pv)
        except (ValueError, tomllib.TOMLDecodeError) as e:
            problems.append(f"pyproject version is unusable: {e}")
        else:
            desc += f"; pyproject says {pv}"
            if pgot != want:
                problems.append(f"pyproject declares {pv} (canonically {pgot}), "
                                f"not the release version {expected} ({want})")
    return problems, desc


# --- self-test ----------------------------------------------------------------

def _lock(*versions: str, name: str = "reflex", trailer: str = "") -> str:
    """A uv.lock with one `[[package]]` per given version, plus a dependency
    entry that must never be mistaken for the root package."""
    head = 'version = 1\nrevision = 3\nrequires-python = ">=3.11, <4.0"\n\n'
    other = ('[[package]]\nname = "pyyaml"\nversion = "6.0.2"\n'
             'source = { registry = "https://pypi.org/simple" }\n\n')
    entry = '[[package]]\nname = "%s"\nversion = "%s"\nsource = { editable = "." }\n\n'
    body = "".join(entry % (name, v) for v in versions)
    return head + other + body + trailer


_PYPROJECT = '[project]\nname = "reflex"\nversion = "%s"\n'


def self_test() -> int:
    cases = [
        # name, lock text, expected version, pyproject text or None, must pass
        ("stamped final: lock and VERSION both 1.1.0",
         _lock("1.1.0"), "1.1.0", None, True),
        ("stamped pre-release: VERSION 1.1.0-rc.1, lock normalized to 1.1.0rc1",
         _lock("1.1.0rc1"), "1.1.0-rc.1", None, True),
        ("all three agree, pyproject included",
         _lock("1.1.0rc1"), "1.1.0-rc.1", _PYPROJECT % "1.1.0-rc.1", True),
        ("THE MEASURED DEFECT: VERSION 1.1.0, lock left at 1.1.0rc1",
         _lock("1.1.0rc1"), "1.1.0", None, False),
        ("the reverse: stamping a pre-release over a final lock",
         _lock("1.1.0"), "1.1.0-rc.1", None, False),
        ("lock a release behind", _lock("1.0.9"), "1.1.0", None, False),
        ("lock agrees but pyproject was not stamped",
         _lock("1.1.0"), "1.1.0", _PYPROJECT % "1.1.0rc1", False),
        ("no reflex entry in the lock", _lock(name="pyyaml-only"), "1.1.0", None, False),
        ("two reflex entries", _lock("1.1.0", "1.1.0"), "1.1.0", None, False),
        ("a lock that does not parse", _lock("1.1.0", trailer="[[[broken\n"), "1.1.0", None, False),
        ("a lock version nothing can read", _lock("not-a-version"), "1.1.0", None, False),
        ("an expected version nothing can read", _lock("1.1.0"), "v-one-point-one", None, False),
    ]
    wrong = 0
    for name, lock_text, expected, pyproject, must_pass in cases:
        problems, desc = check(lock_text, expected, pyproject)
        passed = not problems
        verdict = "ok " if passed == must_pass else "BAD"
        outcome = "PASS" if passed else "FAIL (" + "; ".join(problems) + ")"
        print(f"self-test {verdict} expected {'PASS' if must_pass else 'FAIL'}, got {outcome} -- {name}")
        wrong += passed != must_pass

    # Canonicalization is the load-bearing half and has its own pairs: equal
    # spellings that must collapse, and near-misses that must NOT.
    same = [("1.1.0-rc.1", "1.1.0rc1"), ("1.1.0-RC1", "1.1.0rc1"), ("1.1.0-alpha.2", "1.1.0a2"),
            ("1.1.0-beta1", "1.1.0b1"), ("1.1.0", "1.1.0")]
    differ = [("1.1.0", "1.1.0rc1"), ("1.1.0rc1", "1.1.0rc2"), ("1.1.0", "1.1.1"),
              ("1.1.0a1", "1.1.0b1")]
    for a, b in same:
        if canonical_version(a) != canonical_version(b):
            print(f"self-test BAD '{a}' and '{b}' must canonicalize equal")
            wrong += 1
    for a, b in differ:
        if canonical_version(a) == canonical_version(b):
            print(f"self-test BAD '{a}' and '{b}' must NOT canonicalize equal")
            wrong += 1

    if wrong:
        print(f"self-test: {wrong} case(s) came out wrong -- the lock parity guard cannot be trusted")
        return 1
    print(f"self-test: all {len(cases)} check cases and "
          f"{len(same) + len(differ)} canonicalization pairs came out as they must")
    return 0


# --- CLI ----------------------------------------------------------------------

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_show = sub.add_parser("show")
    p_show.add_argument("lock")
    p_show.add_argument("--package", default="reflex")
    p_check = sub.add_parser("check")
    p_check.add_argument("lock")
    g = p_check.add_mutually_exclusive_group(required=True)
    g.add_argument("--version")
    g.add_argument("--version-file", help="a file whose first line is the version (the repo VERSION)")
    p_check.add_argument("--pyproject")
    p_check.add_argument("--package", default="reflex")
    sub.add_parser("self-test")
    args = ap.parse_args(argv[1:])

    if args.cmd == "self-test":
        return self_test()

    lock_text = _read(args.lock)

    if args.cmd == "show":
        try:
            entries = lock_entries(lock_text, args.package)
        except tomllib.TOMLDecodeError as e:
            print(f"{args.lock}: does not parse as TOML: {e}")
            return 1
        if not entries:
            print(f"{args.lock}: no '{args.package}' package entry")
            return 1
        for e in entries:
            print(f"{args.lock}: {e.get('name')} {e.get('version')} ({e.get('source')})")
        return 0 if len(entries) == 1 else 1

    expected = args.version if args.version else _read(args.version_file).strip()
    pyproject_text = _read(args.pyproject) if args.pyproject else None
    problems, desc = check(lock_text, expected, pyproject_text, args.package)
    if problems:
        for p in problems:
            print(f"FAIL {args.lock}: {p}")
        print(f"     read: {desc}")
        return 1
    print(f"ok   {args.lock}: {desc} -- matches the release version {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
