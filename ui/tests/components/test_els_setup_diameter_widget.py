"""Do the kv bindings this row depends on actually RESOLVE?

The rest of the diameter suite patches `apply_class_lang_rules` away, so every
assertion in it holds even if the kv names a property that does not exist -- a
kv binding resolves lazily at instantiation, so a rename on either side leaves
the dropdown silently empty and unselectable until an operator opens the screen
on the machine.

BUILDING THE REAL TREE IS NOT AVAILABLE HERE, and the reason is worth recording
so nobody keeps re-attempting it: instantiating ElsSetupScreen with its rules
applied SEGFAULTS in this environment (kivy.core.image populate → texture
upload with no GL context). That is why the suite patches the rules away in the
first place.

So this checks the next best thing, statically: every `root.<name>` the screen's
kv references exists on the class. It covers the whole file, not just this row.
"""
import re
from pathlib import Path

import pytest

# Skipped names: attributes reached THROUGH a root reference (root.els.foo
# yields "els", which is checked; the tail is not) need no entry here -- the
# regex only ever captures the first segment.
SCREENS = [
    ("reflex.components.screens.els_setup_screen", "ElsSetupScreen",
     "els_setup_screen.kv"),
    ("reflex.components.screens.axis_screen", "AxisScreen", "axis_screen.kv"),
]


@pytest.mark.parametrize("module_name,class_name,kv_name", SCREENS,
                         ids=[s[1] for s in SCREENS])
def test_every_root_reference_in_the_kv_resolves(module_name, class_name,
                                                 kv_name):
    import importlib
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)

    kv = (Path(mod.__file__).parent / kv_name).read_text(encoding="utf-8")
    kv = "\n".join(ln for ln in kv.splitlines()
                   if not ln.lstrip().startswith("#"))

    names = sorted(set(re.findall(r"\broot\.([A-Za-z_][A-Za-z0-9_]*)", kv)))
    assert names, "the regex matched nothing -- this test would pass on anything"

    missing = [n for n in names if not hasattr(cls, n)]
    assert not missing, f"{class_name}.kv references {missing}, absent on the class"


def test_the_dro_reads_row_is_among_them():
    """Pin the specific names this feature added, so a future refactor that
    drops the row entirely fails here rather than passing a vacuous sweep."""
    from reflex.components.screens.els_setup_screen import ElsSetupScreen
    import reflex.components.screens.els_setup_screen as m

    kv = (Path(m.__file__).parent / "els_setup_screen.kv").read_text(encoding="utf-8")
    for name in ("on_x_dro_reads_selected", "has_x_axis"):
        assert f"root.{name}" in kv, f"the kv no longer references {name}"
        assert hasattr(ElsSetupScreen, name)
