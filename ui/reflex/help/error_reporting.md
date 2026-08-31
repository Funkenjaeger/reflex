Disable Error Reporting
=======================

Controls whether this machine uploads crash reports and performance
traces to a Sentry project.

**Default: ON — nothing is sent.** Reporting is opt-in, and even
switching it off here sends nothing until a destination is configured
(below).

## Behavior

- **ON (default):** No data leaves the machine.
- **OFF:** Reports are sent, *but only if* `REFLEX_SENTRY_DSN` names a
  Sentry project. With no DSN set, this switch does nothing and the log
  says so at startup.

## Configuring a destination

There is deliberately no built-in destination. A DSN identifies an
account, so a built-in one would always be somebody else's — which is
exactly what this setting used to do, reporting to the upstream
project's account rather than yours.

Set the environment variable in `deploy/start.sh`:

    export REFLEX_SENTRY_DSN="https://...@...ingest.sentry.io/..."

Then switch this setting OFF. The startup log states where reports go,
by host and project — never the DSN itself, which contains a key.

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

- Reports are uploaded when they occur, so a machine with no network
  route logs a failed send rather than crashing
- In an air-gapped or secure environment, leave this ON
