"""Error reporting: where it goes, and whether it goes at all.

This file exists because of what it replaced. The Sentry DSN used to be a
string literal in ``app.py``, and ``git log -S`` dates it to c70c7ee0,
2025-07-07, authored by the UPSTREAM project's author before the fork. Reflex
inherited it in the monorepo weld, so every crash report and performance trace
this application produced went to an organisation nobody running Reflex has
access to. Reporting also defaulted to ON, which made that the behaviour of a
fresh install -- and the repository is public now, so it would have been the
behaviour of every install.

Two rules follow, and both are enforced here rather than trusted to a comment:

* **The destination is configuration, never source.** ``REFLEX_SENTRY_DSN``
  names it. Ship no default: a DSN identifies an account, so a built-in one is
  always somebody else's.
* **No DSN means no reporting.** Not a warning, not a fallback -- silence. The
  absence of a destination is a complete answer.

A DSN embeds a credential (``https://<key>@<host>/<project>``), so it is never
logged. :func:`describe_destination` renders the host and project only, which
is what a human needs to answer "where is this going" and carries no secret.
"""

import os
from urllib.parse import urlparse

#: Set this to your own Sentry project's DSN to turn reporting on. Deliberately
#: has no default -- see the module docstring.
DSN_ENV = "REFLEX_SENTRY_DSN"


def sentry_dsn() -> str | None:
    """The configured DSN, or ``None`` when reporting has no destination.

    Whitespace-only is treated as unset: ``REFLEX_SENTRY_DSN=""`` in a unit
    file or a shell export is how somebody turns this off, and it should mean
    off rather than a malformed destination the SDK complains about at boot.
    """
    raw = os.environ.get(DSN_ENV)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def describe_destination(dsn: str) -> str:
    """``host/project`` for a DSN, with the embedded key removed.

    Safe to log. A DSN's userinfo half is a credential; this returns only the
    part that answers where reports are going.
    """
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return "an unparseable destination"
    host = parsed.hostname or "?"
    project = (parsed.path or "").strip("/") or "?"
    return f"{host}/{project}"


def configure(disabled: bool, init=None) -> str:
    """Set up error reporting if it is both wanted and addressed.

    Returns a one-line human-readable status, which the caller logs. Returning
    it rather than logging here keeps the decision testable without capturing
    log output, and makes the states explicit:

    * the operator switched it off  -> nothing happens
    * no DSN is configured          -> nothing happens
    * both satisfied                -> the SDK is initialised

    ``init`` is the injection point for tests; it defaults to
    ``sentry_sdk.init``. Import is deferred so that a machine which never
    reports does not pay for loading the SDK.
    """
    if disabled:
        return "Error reporting is off (disabled in settings)"

    dsn = sentry_dsn()
    if not dsn:
        return (f"Error reporting is off (no {DSN_ENV} set). "
                "Set it to your own Sentry project's DSN to enable it.")

    if init is None:
        import sentry_sdk
        init = sentry_sdk.init

    init(
        dsn=dsn,
        send_default_pii=False,
        # Performance traces, not just crashes. Kept because it is genuinely
        # useful on a machine whose operator has no terminal -- but it is why
        # the help page says "traces" out loud instead of implying that only
        # exceptions leave the machine.
        traces_sample_rate=0.2,
    )
    return f"Error reporting is ON, sending to {describe_destination(dsn)}"
