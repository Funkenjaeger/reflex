import glob
import os
import re

import pytest
from kivy.lang.parser import Parser, ParserException


def get_kv_files():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return glob.glob(os.path.join(project_root, "reflex", "**", "*.kv"), recursive=True)


@pytest.mark.parametrize("kv_file", get_kv_files(), ids=lambda p: os.path.relpath(p))
def test_kv_file_syntax(kv_file):
    """Verify all .kv files have valid KV syntax (catches indentation errors, etc.)."""
    with open(kv_file) as f:
        content = f.read()
    # Strip import directives to avoid triggering module imports during parsing
    lines = content.split("\n")
    filtered = "\n".join(l for l in lines if not l.strip().startswith("#:"))
    try:
        Parser(content=filtered, filename=kv_file)
    except ParserException as e:
        pytest.fail(f"KV parse error in {kv_file}: {e}")


ADVBAR_KV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reflex", "components", "home", "els_advbar.kv",
)


def test_advbar_height_accounts_for_every_collapsible_strip():
    """ElsAdvancedBar must grow by the height of every strip that can appear.

    Regression, 2026-08-16, found on the machine. ElsAdvancedBar is a vertical
    BoxLayout with a fixed base height plus one term per collapsible notice
    strip. The take-up refusal strip was added without adding its term, so when
    it appeared the children totalled more than the parent, overflowed the
    bar's bounds, and the warning rendered over a DRO line in the middle of the
    screen. The strip itself was correct; only this height expression was wrong.

    Deliberately STRUCTURAL rather than a height assertion, for two reasons.
    The widget's kv rule tree cannot be built under test at all -- the mock GL
    backend segfaults on real textures, which is why
    tests/components/test_els_advbar.py patches apply_class_lang_rules out --
    so there is no runtime height to measure. And more usefully, this shape
    catches the NEXT strip somebody adds without touching the height line,
    which an assertion on a hard-coded expected height would not.
    """
    text = open(ADVBAR_KV).read()

    # Child strips collapse via `height: dp(N) if <condition> else 0`.
    strip_conditions = re.findall(
        r"^\s+height:\s*dp\(\d+\)\s+if\s+(.+?)\s+else\s+0\s*$", text, re.M)
    assert strip_conditions, "no collapsible strips found -- has the kv changed shape?"

    # `natural_height`, not `height`: ElsModeLayout owns `height` because it
    # decides whether the bar is shown at all, and in Kivy assigning a property
    # replaces any kv binding on it. The two were the same property until
    # 2026-08-16, when that collision froze the bar at its construction-time
    # height and made this very expression irrelevant -- the kv was correct and
    # simply not driving anything. Two owners, two properties.
    root_height = re.search(r"^  natural_height:\s*(\S.*)$", text, re.M)
    assert root_height, "could not find the ElsAdvancedBar natural_height expression"
    root_expr = root_height.group(1)

    for cond in strip_conditions:
        for prop in sorted(set(re.findall(r"root\.controller\.(\w+)", cond))):
            assert prop in root_expr, (
                f"A collapsible strip is gated on '{prop}', but that property does "
                f"not appear in the ElsAdvancedBar height expression. When that "
                f"strip shows, the bar will not grow to fit it and it will render "
                f"outside the bar -- over whatever happens to be there.\n"
                f"  height: {root_expr}"
            )
