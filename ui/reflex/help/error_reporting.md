Error reporting
===============

This setting controls whether this machine uploads crash reports and
performance traces to a Sentry project.

**Default: OFF. Nothing is sent.** Reporting is opt-in. If you turn
this switch ON, nothing is sent until a destination is configured
(below).

## Behavior

- **OFF (default):** No data leaves the machine.
- **ON:** The machine sends reports *only if* `REFLEX_SENTRY_DSN`
  names a Sentry project. With no DSN set, this switch has no effect.
  The log shows this at startup.

## Configuring a destination

The application has no built-in destination. This is intentional. A
DSN identifies an account, so a built-in DSN would always be the
account of somebody else. Earlier versions of this setting did exactly
that. They sent reports to the account of the upstream project, not to
yours.

Set the environment variable in `deploy/start.sh`:

    export REFLEX_SENTRY_DSN="https://...@...ingest.sentry.io/..."

Then turn this switch ON. The startup log shows where reports go, by
host and project. It never shows the DSN itself, because the DSN
contains a key.

## What Is Collected

- Exception type and stack trace
- Application version
- Platform (Raspberry Pi model, OS version)
- **Performance traces** for a sample of operations (20%), not only
  crashes

It does NOT include:

- Network credentials or passwords
- Position data or machine configurations
- Any personally identifiable information

## Notes

- Reports upload when they occur. If the machine has no network
  route, the log records a failed send. The application does not crash
- In an air-gapped or secure environment, leave this OFF
