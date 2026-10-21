"""The stop-overshoot table as a machine setting (els_overshoot_table).

Pins: the persisted text round-trips and rejects anything that is not an
envelope from (0, 0); an overshoot is rounded UP and a rate to nearest; the
built-in table migrates only at the geometry it was measured under; a table
is stale for exactly one named reason, scale first; an unreadable or
non-string setting loads as no table; and the one-line status reads right.
"""
from fractions import Fraction
from unittest.mock import MagicMock

import pytest

from reflex.fsms import els_overshoot_table as tbl
from reflex.fsms.els_overshoot import DEVELOPMENT_LATHE_TABLE
from reflex.fsms.els_overshoot_table import Binding, StoredTable

F = Fraction
DEV = Binding(F(1, 200), F(127, 64000), "A=1, B=2, C=1/2")


# ─── the text ────────────────────────────────────────────────────────────

def test_format_then_parse_round_trips():
    pts = ((F(0), F(0)), (F("1.4"), F("0.005")), (F("17.645"), F("0.215")))
    text = tbl.format_points(pts)
    assert text == "0:0, 1.4:0.005, 17.645:0.215"
    assert tbl.parse_points(text) == pts


def test_overshoot_rounds_up_and_rate_to_nearest():
    pts = ((F(0), F(0)), (F("1.0000004"), F("0.0000011")))
    assert tbl.format_points(pts) == "0:0, 1:0.000002"
    pts = ((F(0), F(0)), (F("1.0000006"), F("0.0000010")))
    assert tbl.format_points(pts) == "0:0, 1.000001:0.000001"


def test_empty_text_is_no_points():
    assert tbl.parse_points("") == ()
    assert tbl.parse_points("   ") == ()


@pytest.mark.parametrize("text", [
    "0:0, 1.4",                 # missing colon
    "0:0, 1:2:3",               # too many colons
    "0:0, abc:1",               # not a number
    "0:0, 1:x",
    "0:0, 1:1/0",               # zero denominator
    "1:0, 2:1",                 # does not start at (0, 0)
    "0:1, 2:2",
    "0:0, 5:1, 5:2",            # rates not increasing
    "0:0, 5:1, 3:2",
    "0:0, 5:3, 8:2",            # overshoot decreasing
    "0:0",                      # only the origin
])
def test_parse_rejects(text):
    with pytest.raises(tbl.TableTextError):
        tbl.parse_points(text)


# ─── migration ───────────────────────────────────────────────────────────

def test_migration_gives_the_built_in_table_in_counts_exactly():
    t = tbl.migration(DEV)
    assert t is not None
    assert t.in_counts() == DEVELOPMENT_LATHE_TABLE
    assert t.source == tbl.SOURCE_MIGRATED
    assert t.binding == DEV


def test_migration_points_in_mm_end_at_the_known_top():
    t = tbl.migration(DEV)
    assert t.points[-1] == (F("17.645"), F("0.215"))
    assert t.top_rate_mm_s == F("17.645")


def test_migration_ignores_gearbox_text():
    assert tbl.migration(Binding(DEV.mm_per_count, DEV.mm_per_step, "")) is not None


def test_migration_refuses_another_scale():
    assert tbl.migration(Binding(F(1, 400), DEV.mm_per_step, DEV.gearbox)) is None


def test_migration_refuses_another_leadscrew():
    assert tbl.migration(Binding(DEV.mm_per_count, F(1, 1000), DEV.gearbox)) is None


# ─── stale ───────────────────────────────────────────────────────────────

def test_not_stale_when_equal():
    assert tbl.stale_reason(DEV, Binding(DEV.mm_per_count, DEV.mm_per_step, DEV.gearbox)) is None


def test_each_single_difference_is_named():
    assert tbl.stale_reason(DEV, Binding(F(1, 400), DEV.mm_per_step, DEV.gearbox)) == "Z scale changed"
    assert tbl.stale_reason(DEV, Binding(DEV.mm_per_count, F(1, 1000), DEV.gearbox)) == "Leadscrew settings changed"
    assert tbl.stale_reason(DEV, Binding(DEV.mm_per_count, DEV.mm_per_step, "A=2")) == "Gearbox table changed"


def test_scale_reason_wins_when_several_differ():
    other = Binding(F(1, 400), F(1, 1000), "A=2")
    assert tbl.stale_reason(DEV, other) == "Z scale changed"
    other = Binding(DEV.mm_per_count, F(1, 1000), "A=2")
    assert tbl.stale_reason(DEV, other) == "Leadscrew settings changed"


# ─── load and persisted ──────────────────────────────────────────────────

GOOD = dict(table_text="0:0, 1.4:0.005", mm_per_count="1/200",
            mm_per_step="127/64000", gearbox="A=1", source="measured")


def _load(**over):
    args = {**GOOD, **over}
    return tbl.load(args["table_text"], args["mm_per_count"],
                    args["mm_per_step"], args["gearbox"], args["source"])


def test_load_reads_good_strings():
    t = _load()
    assert t.points == ((F(0), F(0)), (F("1.4"), F("0.005")))
    assert t.binding == Binding(F(1, 200), F(127, 64000), "A=1")


@pytest.mark.parametrize("over", [
    {"table_text": ""},
    {"table_text": "garbage"},
    {"table_text": "0:0"},
    {"mm_per_count": ""},
    {"mm_per_count": "0"},
    {"mm_per_count": "-1/200"},
    {"mm_per_count": "abc"},
    {"mm_per_count": "1/0"},
    {"mm_per_step": "0"},
    {"mm_per_step": "x"},
])
def test_load_is_none_for_no_or_unreadable_table(over):
    assert _load(**over) is None


@pytest.mark.parametrize("field", list(GOOD))
def test_load_is_none_for_a_non_string_field(field):
    assert _load(**{field: MagicMock()}) is None
    assert _load(**{field: None}) is None


def test_persisted_then_load_round_trips():
    t = tbl.migration(DEV)
    p = tbl.persisted(t)
    back = tbl.load(p["els_overshoot_table"], p["els_overshoot_mm_per_count"],
                    p["els_overshoot_mm_per_step"], p["els_overshoot_gearbox"],
                    p["els_overshoot_source"])
    assert back == t


# ─── from_counts ─────────────────────────────────────────────────────────

def test_from_counts_never_under_predicts_at_an_awkward_resolution():
    b = Binding(F(1, 254), F(127, 64000), "")
    counts = ((0, 0), (333, 7), (777, 19), (1234, 41))
    t = tbl.from_counts(counts, b, "measured")
    for (r_in, o_in), (r_out, o_out) in zip(counts, t.in_counts()):
        assert o_out >= o_in
    assert t.in_counts()[0] == (0, 0)


# ─── status line ─────────────────────────────────────────────────────────

def test_status_none_stored():
    assert tbl.status_text(None, None) == "None stored"


def test_status_stale_lowercases_the_reason():
    t = tbl.migration(DEV)
    assert tbl.status_text(t, "Z scale changed").startswith("Stale: z scale changed")


def test_status_migrated():
    assert tbl.status_text(tbl.migration(DEV), None) == "Saved from the development lathe, up to 17.6 mm/s"


def test_status_measured():
    t = StoredTable(((F(0), F(0)), (F(3), F("0.02"))), DEV, "measured 2026-10-11 in A")
    s = tbl.status_text(t, None)
    assert s.startswith("Measured")
    assert s.endswith("up to 3.0 mm/s")
