"""Static guard: every SavingDispatcher subclass is constructed with an
explicit id_override.

WHY THIS EXISTS. SavingDispatcher.id_override defaults to f"{self.uid}" -- a
Kivy widget uid, which is allocation-order dependent -- and it names the
per-widget YAML file the dispatcher saves to. So any layout change silently
orphans the settings file and the widget comes back to defaults. ElsAdvancedBar
shipped without an id_override for the app's whole life; found via task
6a935c94 on 2026-08-29 and fixed in 7944c77.

Pure ast, no kivy import needed: parses production source under reflex/ (not
tests/ or previews/) and flags any construction of a known subclass that
carries no id_override keyword.

WHAT MAKES THIS GUARD TRUSTWORTHY rather than a check that cannot fail:
  * test_the_guard_flags_the_original_defect and its siblings run the scanner
    against synthetic source whose answer is known, so the scanner is proven
    to go red -- the production sweep passing means something only because
    these pass too;
  * test_the_allow_list_is_the_real_hierarchy derives the subclass set from
    the source instead of trusting the hand-maintained literal below, which
    is the way a static allow-list normally rots (a new subclass is added,
    nobody extends the list, and the guard reports clean forever).
"""
import ast
from pathlib import Path

import pytest

# Known SavingDispatcher subclasses. Hand-maintained for the SCAN, but
# test_the_allow_list_is_the_real_hierarchy below proves it still matches the
# code -- so a new subclass fails the suite instead of silently escaping.
SAVING_DISPATCHER_SUBCLASSES = {
    "ElsAdvancedBar", "ElsBar", "AxisDispatcher", "CirclePatternDispatcher",
    "RectPatternDispatcher", "LinePatternDispatcher", "ElsDispatcher",
    "FormatsDispatcher", "ServoDispatcher", "InputDispatcher",
}

BASE_CLASS = "SavingDispatcher"
REFLEX_ROOT = Path(__file__).resolve().parents[2] / "reflex"


def _called_name(func):
    """The bare class name being called, for both `Foo()` and `mod.Foo()`.

    The attribute form matters: the codebase imports these directly today, but
    a guard that only understands ast.Name would go quietly blind the first
    time somebody writes `home.ElsBar(...)`, and a guard that goes blind
    without failing is worse than no guard.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _scan(source: str, label: str) -> list[str]:
    """Offenders in one parsed source. Split out so the tests below can aim
    it at source whose correct answer is known."""
    offenders = []
    for node in ast.walk(ast.parse(source, filename=label)):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node.func)
        if name in SAVING_DISPATCHER_SUBCLASSES:
            if not any(kw.arg == "id_override" for kw in node.keywords):
                offenders.append(
                    f"{label}:{node.lineno}: {name}(...) missing id_override")
    return offenders


def _production_sources():
    for path in sorted(REFLEX_ROOT.rglob("*.py")):
        # encoding is explicit: this codebase is full of em dashes and the
        # platform default is not UTF-8 everywhere the suite is run from.
        yield path.read_text(encoding="utf-8"), str(path.relative_to(REFLEX_ROOT))


# ── the guard itself ─────────────────────────────────────────────────

def test_every_saving_dispatcher_construction_passes_id_override():
    offenders = []
    for source, label in _production_sources():
        offenders += _scan(source, label)
    assert not offenders, (
        "SavingDispatcher subclass constructed without id_override "
        "(uid-keyed config file, orphans on the next layout change):\n"
        + "\n".join(offenders)
    )


# ── proof the guard can go red ───────────────────────────────────────

def test_the_guard_flags_the_original_defect():
    """The exact call that shipped broken, from els_mode_layout.py."""
    offenders = _scan("ElsAdvancedBar(els_bar=els_bar)\n", "synthetic.py")
    assert len(offenders) == 1
    assert "ElsAdvancedBar" in offenders[0]


def test_the_guard_flags_an_attribute_call():
    """`mod.ElsBar(...)` is the same defect wearing a dotted name."""
    offenders = _scan("home.ElsBar(orientation='vertical')\n", "synthetic.py")
    assert len(offenders) == 1
    assert "ElsBar" in offenders[0]


@pytest.mark.parametrize("source", [
    "ElsAdvancedBar(els_bar=els_bar, id_override='0')",
    "home.ElsBar(id_override='0')",
    "AxisDispatcher(id_override=f'{index}')",
    # Not a dispatcher at all -- the scan must not widen.
    "BoxLayout(orientation='vertical')",
    "some_function(els_bar=els_bar)",
])
def test_the_guard_is_silent_when_it_should_be(source):
    assert _scan(source + "\n", "synthetic.py") == []


# ── proof the allow-list has not rotted ──────────────────────────────

def _declared_subclasses() -> set[str]:
    """Every class in reflex/ that reaches SavingDispatcher through its bases.

    A fixpoint rather than a single pass, so a subclass OF a subclass counts.
    There are none today; the point is that adding one does not create a hole.
    """
    direct: dict[str, set[str]] = {}
    for source, label in _production_sources():
        for node in ast.walk(ast.parse(source, filename=label)):
            if isinstance(node, ast.ClassDef):
                bases = {_called_name(b) or getattr(b, "id", None)
                         for b in node.bases}
                direct[node.name] = {b for b in bases if b}

    found = {BASE_CLASS}
    changed = True
    while changed:
        changed = False
        for name, bases in direct.items():
            if name not in found and bases & found:
                found.add(name)
                changed = True
    return found - {BASE_CLASS}


def test_the_allow_list_is_the_real_hierarchy():
    """The one assertion that keeps the hand-maintained set honest.

    Without this, adding an eleventh SavingDispatcher subclass leaves the
    guard reporting a confident clean over source it never looks at.
    """
    actual = _declared_subclasses()
    missing = actual - SAVING_DISPATCHER_SUBCLASSES
    phantom = SAVING_DISPATCHER_SUBCLASSES - actual
    assert not missing, (
        "new SavingDispatcher subclass not in this file's allow-list, so it "
        f"is NOT being checked: {sorted(missing)}")
    assert not phantom, (
        "allow-list names classes that no longer exist, so those entries "
        f"check nothing: {sorted(phantom)}")
