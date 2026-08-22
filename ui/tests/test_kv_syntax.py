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


def test_advbar_notice_overlay_accounts_for_every_collapsible_strip():
    """Every strip that can appear must be in the notice OVERLAY's height.

    Regression, 2026-08-16, found on the machine: a strip that takes height
    without its container growing to match overflows the container's bounds and
    renders wherever the arithmetic lands it -- observed as the take-up warning
    drawn over a DRO line in the middle of the screen.

    RETARGETED 2026-08-22. The strips used to add their height to the BAR
    (`natural_height`), which made the bar grow and pushed the DRO rows from 99
    to 89 px every time a warning appeared. They now live in a `notice_overlay`
    pinned to the bar's top edge and drawn OVER the control row, so nothing on
    the screen resizes. The overflow hazard did not go away -- it moved one
    level down, to the overlay -- so the guard moves with it.

    Deliberately STRUCTURAL. The widget's kv rule tree cannot be built under
    test (the mock GL backend segfaults on real textures, which is why
    tests/components/test_els_advbar.py patches apply_class_lang_rules out), so
    there is no runtime height to measure here. Layout is verified by rendering
    instead: previews/preview_takeup_banner.py.
    """
    text = open(ADVBAR_KV).read()

    strip_conditions = re.findall(
        r"^\s+height:\s*dp\(\d+\)\s+if\s+(.+?)\s+else\s+0\s*$", text, re.M)
    assert strip_conditions, "no collapsible strips found -- has the kv changed shape?"

    overlay = re.search(r"^\s+id:\s*notice_overlay\s*$.*?^\s+height:\s*(\S.*)$",
                        text, re.M | re.S)
    assert overlay, "could not find the notice_overlay height expression"
    overlay_expr = overlay.group(1)

    # `natural_height` must NOT grow with a strip any more: growing is exactly
    # the resizing this design removed.
    root_height = re.search(r"^  natural_height:\s*(\S.*)$", text, re.M)
    assert root_height and "if" not in root_height.group(1), (
        "ElsAdvancedBar.natural_height must be constant -- notice strips overlay "
        "the controls rather than growing the bar, so that the DRO rows above "
        f"never resize. Found: {root_height.group(1) if root_height else None}"
    )

    # Only controller-gated strips belong to the overlay; the wizard
    # instruction strip is gated on `root.enable_wizard` and is part of the
    # bar's CONTENT, carved out of the 128 px base.
    for cond in strip_conditions:
        for prop in sorted(set(re.findall(r"root\.controller\.(\w+)", cond))):
            assert prop in overlay_expr, (
                f"A collapsible notice strip is gated on '{prop}', but that "
                f"property does not appear in the notice_overlay height "
                f"expression. When that strip shows, the overlay will not grow "
                f"to fit it and it will render outside it -- over whatever "
                f"happens to be there.\n  height: {overlay_expr}"
            )


def test_advbar_has_no_size_hint_y_of_zero():
    """`size_hint_y: 0` must not appear in the advanced bar. It is a trap.

    Regression, 2026-08-22, found on the machine and then reproduced headlessly
    (previews/preview_takeup_banner.py). Kivy's `size_hint_y: 0` does NOT set a
    widget's height to 0 -- it removes the child from the box's proportional
    distribution while the widget KEEPS whatever height it last had. The wizard
    instruction strip used `0.2 if wizard else 0`, so in stop-only mode it held
    26 px it was no longer allocated while the control row below took the full
    remainder, the children summed to 184 px inside a 158 px bar, and the
    overflow pushed the take-up warning out of the top of the bar and onto the
    spindle DRO row. A forced do_layout() did not clear it.

    It only bit in stop-only: in wizard mode the hints sum to 1.0 with nothing
    stale, which is why six attempts and a screenshot in the wrong mode all
    looked fine. Collapse with `size_hint_y: None` + `height: ... else 0`.
    """
    text = open(ADVBAR_KV).read()
    offenders = re.findall(r"^(\s+size_hint_y:\s*0\s*$|\s+size_hint_y:.*\belse\s+0\s*$)",
                           text, re.M)
    assert not offenders, (
        "size_hint_y collapsing to 0 in els_advbar.kv. A hint of 0 leaves the "
        "widget's height untouched, so the bar over-allocates and its notice "
        "strips render outside it. Use size_hint_y: None with an explicit "
        "height instead.\n  " + "\n  ".join(o.strip() for o in offenders)
    )
