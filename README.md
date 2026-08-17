# reflex

Electronic leadscrew for a manual lathe: STM32F411 firmware and a Kivy
touchscreen UI, one system, one repository.

| Path | What it is |
|---|---|
| `fw/` | STM32F411 firmware (100 kHz motion ISR, FreeRTOS, Modbus RTU slave). Formerly the `reflex-fw` repository. |
| `ui/` | Kivy touchscreen app for the Raspberry Pi at the machine (`elspi`), Modbus master. Formerly the `reflex-ui` repository. |

## Provenance

Welded from the two constituent repositories on 2026-08-17, with **full history
preserved on both sides** — every historical commit is here, rewritten
path-consistently under `fw/` and `ui/` (so `git log -S`/`--follow` work scoped
to either subtree, all the way back through the pre-fork era). Old-repo tags
carry `fw-` / `ui-` prefixes. The original repositories remain frozen as
archives; nothing new lands there.

Rationale and design: `ui/docs/decisions/repo-structure-monorepo.md` (subtree
layout, lockstep versioning) and the 2026-08-16 architecture review — the
UI↔firmware register contract is under active refactor, and every contract
change is now one commit instead of a coordinated pair. reflex is a hard fork
of `rotary-controller-f4`; there is no upstream relationship to preserve.

## Tests

```bash
# UI unit suites (from ui/, Linux/WSL only — the venv is a Linux venv)
uv run --frozen pytest -q

# firmware emulator suite (real firmware C against a host shim)
cmake -B fw/emulator/build fw/emulator && cmake --build fw/emulator/build -j
ctest --test-dir fw/emulator/build --output-on-failure

# full-stack system tests (UI driving the compiled firmware emulator)
cd ui && uv run --frozen pytest -m system tests/system -q
```

The register-map contract test compares `ui/reflex/utils/devices.py` against
`fw/Core/Inc/Ramps.h` and can no longer skip: the firmware is always checked
out, because it lives here.

## Deployment note

The machine (`elspi`) flashes firmware and runs the UI as **separately deployed
artifacts** — a power cycle is required for new firmware to take effect. The
running pair can therefore lag any commit; `protocolVersion` and `diagSchema`
checks guard that seam and are permanent, monorepo or not.
