"""Cross-half registry contract: diag schema ids and machine-mode wire values.

The register-map contract test (test_register_map_contract.py) compares struct
LAYOUT and says nothing about the two value registries the halves also mirror
by hand:

- **Diag schema ids** — ``fw/Core/Inc/Ramps.h`` ``ELS_DIAG_SCHEMA_*`` versus
  ``ui/reflex/utils/devices.py``'s constants and ``els_diag.KNOWN_SCHEMAS``.
  Nothing else cross-checks these two registries; this test is the check. A
  probe registered on one side and forgotten on
  the other passes CI green and fails silently at the lathe (the recorder
  goes dormant against an id it does not recognise).
- **ELS command channel values** — ``fw/Core/Inc/els_command.h``
  ``ELS_CMD_*`` (command ids and ``ELS_CMD_RESULT_*`` outcomes) versus
  ``ui/reflex/fsms/els_command.py`` ``CMD_*``. A result code the UI does not
  know is shown as a refusal by range, so a drift here would turn a success
  into a fallback teardown, or a refusal into a silent success.
- **Machine-mode wire values** — ``fw/Core/Inc/els_machine_mode.h``
  ``ELS_MMODE_*`` versus ``ui/reflex/fsms/els_mode_watch.py`` ``MODE_*``.
  Both sides pin their own copy as literals; until this test, nothing
  compared the copies to each other — the exact hand-mirrored-registry shape
  the 2026-08-16 architecture review catalogued (and the monorepo makes
  checkable in one read).

Same skip contract as the register-map test: runs in the DEFAULT suite; in
the monorepo the firmware is always present, so a skip can only mean a broken
resolver (CI sets REFLEX_FW_REQUIRED=1, which turns that into a failure).
"""

import re

import reflex.fsms.els_mode_watch as mode_watch
import reflex.utils.devices as devices
from reflex.fsms.els_diag import KNOWN_SCHEMAS

from tests.fw_repo import require_or_skip_reason
import pytest

FW_DIR, SKIP_REASON = require_or_skip_reason()
pytestmark = pytest.mark.skipif(FW_DIR is None, reason=SKIP_REASON or "")


def _parse_defines(text, prefix):
    """{NAME: int} for every `#define <prefix><NAME> <int>[u]` in the text."""
    out = {}
    for m in re.finditer(
        rf"^#define\s+{prefix}(\w+)\s+(\d+)u?\b", text, re.MULTILINE
    ):
        out[m.group(1)] = int(m.group(2))
    return out


def _fw_schemas():
    text = (FW_DIR / "Core" / "Inc" / "Ramps.h").read_text(encoding="utf-8")
    return _parse_defines(text, "ELS_DIAG_SCHEMA_")


def _fw_modes():
    text = (FW_DIR / "Core" / "Inc" / "els_machine_mode.h").read_text(
        encoding="utf-8"
    )
    return _parse_defines(text, "ELS_MMODE_")


# ── diag schema ids ────────────────────────────────────────────────────


def test_every_fw_schema_id_is_mirrored_with_the_same_value():
    fw = _fw_schemas()
    assert fw, "parsed no ELS_DIAG_SCHEMA_* defines — parser or header moved"
    for name, value in fw.items():
        ui_name = f"ELS_DIAG_SCHEMA_{name}"
        assert hasattr(devices, ui_name), (
            f"firmware defines {ui_name}={value} but devices.py does not mirror "
            f"it — a probe registered on one side only fails silently at the "
            f"lathe (DIAG.md step 5)"
        )
        assert getattr(devices, ui_name) == value, (
            f"{ui_name}: firmware says {value}, devices.py says "
            f"{getattr(devices, ui_name)}"
        )


def test_no_ui_schema_id_exists_without_a_firmware_side():
    fw = _fw_schemas()
    ui_names = [n for n in dir(devices) if n.startswith("ELS_DIAG_SCHEMA_")]
    for ui_name in ui_names:
        assert ui_name.removeprefix("ELS_DIAG_SCHEMA_") in fw, (
            f"devices.py defines {ui_name} but the firmware does not — a "
            f"schema id is a wire contract and the firmware is its registry"
        )


def test_isr_gap_word_map_matches_the_probe_header():
    """Schema 8 packs its window into diagTrace words by index, and the UI
    decodes them by index. A drifted index decodes a neighbour's value under
    the right name, so compare every one."""
    import reflex.fsms.els_diag as els_diag
    text = (FW_DIR / "Core" / "Inc" / "els_diag_isr_gap.h").read_text(
        encoding="utf-8"
    )
    fw = _parse_defines(text, "ELS_DIAG_GAP_W_")
    assert len(fw) >= 12, "parsed too few ELS_DIAG_GAP_W_* defines"
    for name, value in fw.items():
        ui = getattr(els_diag, f"ISR_GAP_W_{name}", None)
        assert ui == value, (
            f"ELS_DIAG_GAP_W_{name}={value} but els_diag.ISR_GAP_W_{name}={ui}"
        )
    thresholds = _parse_defines(text, "ELS_DIAG_GAP_")["THRESHOLDS"]
    assert els_diag.ISR_GAP_THRESHOLDS == thresholds


def test_known_schemas_are_defined_ids_and_never_none():
    fw_values = set(_fw_schemas().values())
    for schema in KNOWN_SCHEMAS:
        assert schema in fw_values, (
            f"KNOWN_SCHEMAS contains {schema}, which no firmware "
            f"ELS_DIAG_SCHEMA_* define names"
        )
    assert devices.ELS_DIAG_SCHEMA_NONE not in KNOWN_SCHEMAS, (
        "0 means 'no probe'; recording under it would store meaningless zeros"
    )


# ── ELS command channel values ──────────────────────────────────────────


def _fw_command_values():
    text = (FW_DIR / "Core" / "Inc" / "els_command.h").read_text(encoding="utf-8")
    return _parse_defines(text, "ELS_CMD_")


def test_command_values_match_exactly_in_both_directions():
    import reflex.fsms.els_command as els_command
    fw = _fw_command_values()
    assert {"NONE", "DISENGAGE", "RESULT_OK"} <= set(fw), (
        "parsed too few ELS_CMD_* defines -- parser or header moved")
    ui = {
        n.removeprefix("CMD_"): getattr(els_command, n)
        for n in dir(els_command)
        if n.startswith("CMD_") and isinstance(getattr(els_command, n), int)
    }
    assert ui == fw, (
        f"command channel registries diverged (append, never renumber):\n"
        f"  firmware: {dict(sorted(fw.items(), key=lambda kv: kv[1]))}\n"
        f"  ui:       {dict(sorted(ui.items(), key=lambda kv: kv[1]))}"
    )


def test_every_result_is_classified_by_its_range():
    """Successes are below 0x100 and refusals at or above it; the UI's
    result_is_ok is that rule, so a code it does not know still lands on the
    right side."""
    import reflex.fsms.els_command as els_command
    for name, value in _fw_command_values().items():
        if not name.startswith("RESULT_"):
            continue
        want = name.startswith("RESULT_OK")
        assert els_command.result_is_ok(value) is want, (name, value)
    assert set(els_command.RESULT_NAMES) == {
        v for n, v in _fw_command_values().items() if n.startswith("RESULT_")}


# ── machine-mode wire values ───────────────────────────────────────────


def test_mode_values_match_exactly_in_both_directions():
    fw = _fw_modes()
    assert fw, "parsed no ELS_MMODE_* defines — parser or header moved"
    ui = {
        n.removeprefix("MODE_"): getattr(mode_watch, n)
        for n in dir(mode_watch)
        if n.startswith("MODE_") and isinstance(getattr(mode_watch, n), int)
    }
    assert ui == fw, (
        f"machine-mode registries diverged (append, never renumber):\n"
        f"  firmware: {dict(sorted(fw.items(), key=lambda kv: kv[1]))}\n"
        f"  ui:       {dict(sorted(ui.items(), key=lambda kv: kv[1]))}"
    )


def test_mode_names_cover_every_wire_value():
    fw = _fw_modes()
    assert set(mode_watch.MODE_NAMES) == set(fw.values()), (
        "MODE_NAMES must name every published mode — an unnamed mode renders "
        "as UNKNOWN(n) in weeks of census logs"
    )
