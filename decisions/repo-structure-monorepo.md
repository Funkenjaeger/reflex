# One repository for firmware and UI

## Context

Reflex is STM32 firmware and a Raspberry Pi touchscreen UI that talk over a
Modbus register map. They began as two repositories, but every interface change
needed a matching commit in each, and nothing kept the pair in step except
convention.

## Decision

One repository with `fw/` and `ui/`, released together under one version. CI
is filtered by path. Both histories were kept, and the old repositories are
archived.

## Why

- A version number names a firmware and UI pair that were tested together.
- A register-map change lands, and reverts, as one commit. One schema now
  generates both the C struct and the Python decoder.
- Rejected: separate versions per half (it undoes the point), a git submodule
  (daily friction for little gain at this size), and staying split (nothing
  stops a half-reverted pair).

## Consequences

Every release carries both halves even when only one changed, and releases are
cut deliberately with an explicit version.
