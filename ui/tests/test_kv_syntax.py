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


def _kv_widget_block(lines, widget_id):
    """Bound a widget's kv rule by indentation, keyed on its unique `id:` line.

    Returns (start, end, prop_indent): the rule spans lines[start:end], from
    the widget's declaration line to the first non-blank line indented
    shallower than its `id:` line -- the first thing past the rule, whether
    that is a sibling widget or the outdented comment introducing one. The
    widget's own properties sit at exactly prop_indent; anything deeper
    belongs to a child.
    """
    id_pat = re.compile(rf"^(\s+)id:\s*{re.escape(widget_id)}\s*$")
    hits = [(i, len(m.group(1))) for i, line in enumerate(lines)
            if (m := id_pat.match(line))]
    assert len(hits) == 1, (
        f"expected exactly one 'id: {widget_id}' in els_advbar.kv, "
        f"found {len(hits)}")
    id_line, prop_indent = hits[0]

    start = id_line
    while start > 0:
        prev = lines[start - 1]
        start -= 1
        if prev.strip() and len(prev) - len(prev.lstrip()) < prop_indent:
            break  # the widget declaration line itself
    end = len(lines)
    for j in range(id_line + 1, len(lines)):
        line = lines[j]
        if line.strip() and len(line) - len(line.lstrip()) < prop_indent:
            end = j
            break
    return start, end, prop_indent


def _own_prop(lines, start, end, prop_indent, name):
    """The widget's OWN `name:` line inside its rule: exactly prop_indent deep.

    Unambiguous by kv structure: children's properties are deeper, child
    declarations are `ClassName:` with no value, and canvas instruction
    properties are deeper still.
    """
    pat = re.compile(rf"^ {{{prop_indent}}}{re.escape(name)}:\s*(\S.*)$")
    for i in range(start, end):
        m = pat.match(lines[i])
        if m:
            return i, m.group(1)
    return None, None


def _own_height(lines, start, end, prop_indent):
    """The widget's OWN height line. See _own_prop."""
    return _own_prop(lines, start, end, prop_indent, "height")


def test_advbar_notice_overlay_accounts_for_every_collapsible_strip():
    r"""Every transient strip that can appear must be in the notice OVERLAY's height.

    Regression, 2026-08-16, found on the machine: a strip that takes height
    without its container growing to match overflows the container's bounds and
    renders wherever the arithmetic lands it -- observed as the take-up warning
    drawn over a DRO line in the middle of the screen.

    RETARGETED 2026-08-22. The strips used to add their height to the BAR
    (`natural_height`), which made the bar grow and pushed the DRO rows from 99
    to 89 px every time a warning appeared (99 was the row height then; it is
    101 since 2026-08-29 -- see test_els_mode_layout.py, a deliberate one-time
    change that does not touch what is guarded here). They now live in a
    `notice_overlay` pinned to the bar's top edge and drawn OVER the control
    row, so nothing on the screen resizes. The overflow hazard did not go away
    -- it moved one
    level down, to the overlay -- so the guard moves with it.

    REPAIRED 2026-08-25: this check used to be structurally unable to fail.
    The overlay height expression was captured with `(\S.*)$` under re.S --
    greedy across newlines -- so the "expression" each strip was checked
    against was the remainder of the FILE (11 kB, 219 lines), and any strip
    declared after the overlay's height line satisfied the check by simply
    existing: its own gating line sat inside the capture. Since every strip
    is a CHILD of notice_overlay, declared after that line, no missing term
    could ever go red. The capture is now bounded to the overlay's kv rule by
    indentation (_kv_widget_block), and each controller-gated strip must
    additionally BE inside that rule -- a strip parked anywhere else takes
    height no overlay accounts for, which is the same defect by another
    route.

    NARROWED 2026-08-29. There used to be a second overlay carrying the
    PERSISTENT status tenants, and this guard had to skip its collapse
    expression by line number. That overlay is gone: the phase offset and the
    reference latch are printed in a permanently reserved gutter instead of
    overlaid on the field headers for a whole job (see the test below, which
    inherited what it was defending). notice_overlay is now the only overlay,
    so a controller-gated collapsible strip anywhere outside it is
    unconditionally a defect -- there is no longer a second legitimate home to
    excuse one.

    Deliberately STRUCTURAL. The widget's kv rule tree cannot be built under
    test (the mock GL backend segfaults on real textures, which is why
    tests/components/test_els_advbar.py patches apply_class_lang_rules out), so
    there is no runtime height to measure here. Layout is verified by rendering
    instead: previews/preview_takeup_banner.py.
    """
    text = open(ADVBAR_KV).read()
    lines = text.split("\n")

    # A strip's height is `<something> if <cond> else 0`, where <something> is
    # either a dp() literal or a reference to another widget's height. The
    # reference form arrived 2026-08-29 when the notice strips were tied to
    # `status_gutter.height`; matching only dp() would have quietly emptied
    # this list and turned the whole guard green by finding nothing to check.
    strips = [(i, m.group(2)) for i, line in enumerate(lines)
              if (m := re.match(
                  r"^(\s+)height:\s*(?:dp\(\d+\)|[\w.]+)\s+if\s+(.+?)\s+else\s+0\s*$",
                  line))]
    assert strips, "no collapsible strips found -- has the kv changed shape?"

    n_start, n_end, n_indent = _kv_widget_block(lines, "notice_overlay")
    notice_height_line, overlay_expr = _own_height(lines, n_start, n_end,
                                                   n_indent)
    assert overlay_expr, "could not find the notice_overlay height expression"

    # `natural_height` must NOT grow with a strip any more: growing is exactly
    # the resizing this design removed.
    root_height = re.search(r"^  natural_height:\s*(\S.*)$", text, re.M)
    assert root_height and "if" not in root_height.group(1), (
        "ElsAdvancedBar.natural_height must be constant -- notice strips overlay "
        "the controls rather than growing the bar, so that the DRO rows above "
        f"never resize. Found: {root_height.group(1) if root_height else None}"
    )

    for line_no, cond in strips:
        # Only controller-gated strips belong to the overlay; the wizard
        # instruction strip is gated on `root.enable_wizard` and is part of
        # the bar's CONTENT, carved out of the 128 px base.
        props = sorted(set(re.findall(r"root\.controller\.(\w+)", cond)))
        if not props:
            continue
        if line_no == notice_height_line:
            # The overlay's OWN height: the container's collapse, not a strip
            # in need of carrying.
            continue
        assert n_start <= line_no < n_end, (
            f"els_advbar.kv line {line_no + 1}: a controller-gated collapsible "
            f"strip is declared outside the notice_overlay rule. Transient "
            f"strips must be CHILDREN of notice_overlay so its height "
            f"expression can carry them; persistent state is PRINTED in the "
            f"permanent status gutter, not collapsed into a strip (see the "
            f"overlay note in the kv). Anywhere else, the strip takes height "
            f"no overlay accounts for and renders over whatever happens to be "
            f"there.\n"
            f"  {lines[line_no].strip()}"
        )
        for prop in props:
            assert prop in overlay_expr, (
                f"A collapsible notice strip is gated on '{prop}', but that "
                f"property does not appear in the notice_overlay height "
                f"expression. When that strip shows, the overlay will not grow "
                f"to fit it and it will render outside it -- over whatever "
                f"happens to be there.\n  height: {overlay_expr}"
            )


def test_advbar_notice_strips_are_measured_in_gutters_not_in_literals():
    """A notice strip is one status gutter tall, expressed as one.

    2026-08-29, found by looking at a render. The strips were dp(30) each, a
    literal chosen when the top of this bar was a wizard instruction strip and
    nothing was underneath it. The status gutter is dp(26) with a 1 px inset,
    so a take-up warning painted 30 px of translucent red over a 25 px band
    and spilled the remainder down over the top of the controls -- measured on
    the flattened render as red reaching image row 373 against a gutter that
    ends at 369. Evan: it "ought to stay bounded to the gutter".

    THE FIX IS THE DERIVATION, NOT THE NUMBER. Setting the literal to dp(26)
    would have looked identical today and drifted again the next time the
    gutter moved -- which is precisely how it broke the first time. So what is
    guarded here is that no notice strip states its own height: each must be
    written in terms of `status_gutter.height`, so the two cannot disagree.

    Structural for the usual reason (no rule tree under the mock GL backend);
    the pixels are checked by previews/preview_phase_offset.py, which prints
    the strip's rect against the gutter's.
    """
    lines = open(ADVBAR_KV).read().split("\n")
    n_start, n_end, n_indent = _kv_widget_block(lines, "notice_overlay")

    checked = 0
    for strip_id in ("notice_overlay", "reframe_notice", "takeup_notice"):
        s_start, s_end, s_indent = _kv_widget_block(lines, strip_id)
        if strip_id != "notice_overlay":
            assert n_start <= s_start < n_end, (
                f"{strip_id} is not inside notice_overlay any more")
        _, expr = _own_height(lines, s_start, s_end, s_indent)
        assert expr, f"could not find {strip_id}'s height expression"
        assert "status_gutter.height" in expr, (
            f"{strip_id}'s height must be written in terms of "
            f"status_gutter.height. A notice overlays the gutter, so a strip "
            f"that states its own number will spill past the divider and over "
            f"the controls the moment the gutter changes -- which is what "
            f"dp(30) against a 25 px band did.\n  height: {expr}")
        assert not re.search(r"\bdp\(\d+\)", expr), (
            f"{strip_id}'s height still contains a dp() literal. The gutter is "
            f"the only place its height may be stated.\n  height: {expr}")
        checked += 1
    assert checked == 3, "expected three notice heights to check"


def test_advbar_status_gutter_is_permanent_and_carries_both_persistent_tenants():
    """Neither persistent tenant may end up in a container that can vanish.

    RETARGETED 2026-08-29, same property, new structure. It used to read
    "every persistent tenant must appear in the STATUS overlay's height",
    because the phase offset and the thread-reference latch shared one
    collapsible band overlaid on the field headers, and a tenant left out of
    that band's height expression sat in a zero-height container -- rendered
    nowhere while the controller happily reported it shown, which for the
    latch was the original 2026-08-24 invisible-latch defect reintroduced
    silently.

    The band is gone. Both tenants are now PRINTED in `status_gutter`, a
    permanently reserved dp(26) strip carved out of the bar's own 128 px, so
    they cover nothing and nothing has to be traded against them. What did NOT
    change is the thing this test was ever really defending: a persistent
    tenant must not be parked inside a container that can collapse or be
    hidden underneath it. The gutter is where that can now go wrong, and it
    can go wrong more quietly than the band could -- the gutter's height line
    spent its whole previous life reading `dp(26) if root.enable_wizard
    else 0`, so restoring that gate is a one-token edit that would blank both
    chips in stop-only, the mode the machine is actually run in, while every
    controller property still said "shown".

    So: the container must be unconditional and ungated, both tenants must
    live inside it, and each tenant's own visibility must collapse the CHIP
    rather than the container. The reference chip is additionally checked to
    have no visibility gate at all -- "no reference" is an answer the operator
    needs printed, and an indicator that is only there when the answer is yes
    cannot be trusted to be there at all -- and to keep its relevance dim,
    which is what distinguishes a reference that is latched-but-not-in-use
    (feed mode) from one that is gone.

    Deliberately STRUCTURAL, like the notice_overlay guard above and for the
    same reason: the kv rule tree cannot be built under the mock GL backend,
    so there is no runtime height to measure. Layout is verified by rendering
    instead: previews/preview_phase_offset.py.
    """
    text = open(ADVBAR_KV).read()
    lines = text.split("\n")

    g_start, g_end, g_indent = _kv_widget_block(lines, "status_gutter")
    _, gutter_height = _own_height(lines, g_start, g_end, g_indent)
    assert gutter_height, "could not find the status_gutter height expression"

    # 1. The container is unconditional. Not "is 26 px" -- the number is a
    #    layout choice -- but "cannot become 0", which is what would hide the
    #    tenants.
    assert "if" not in gutter_height and "else" not in gutter_height, (
        "status_gutter's height must be unconditional. A mode gate here puts "
        "both persistent status chips inside a zero-height container -- "
        "rendered nowhere while the controller reports them shown. The 26 px "
        "is reserved out of the bar's own 128, so nothing else resizes when "
        "it is kept.\n"
        f"  height: {gutter_height}"
    )

    # 2. ...and not hidden wholesale either, which collapses the tenants just
    #    as thoroughly without touching the height.
    for gate in ("opacity", "disabled"):
        line_no, expr = _own_prop(lines, g_start, g_end, g_indent, gate)
        assert line_no is None, (
            f"status_gutter must not carry its own '{gate}' gate: it hides "
            f"both persistent status chips at once, which is the zero-height "
            f"container by another route. Gate the CONTENTS instead -- the "
            f"instruction label and each chip gate themselves.\n"
            f"  {gate}: {expr}"
        )

    # 3. Both tenants are in there.
    gutter_body = "\n".join(lines[g_start:g_end])
    for prop in ("phase_offset_active", "thread_ref_latched"):
        assert prop in gutter_body, (
            f"'{prop}' is a persistent status tenant but nothing in the "
            f"status_gutter rule references it. Persistent job state is "
            f"printed in the gutter; it no longer has an overlay to live in."
        )

    # 4. The phase chip gates ITSELF, on the controller's flag rather than on
    #    its text being non-empty -- an offset that cannot be converted still
    #    has to show, saying so, instead of reading as "no offset".
    p_start, p_end, p_indent = _kv_widget_block(lines, "chip_phase")
    assert g_start <= p_start < g_end, "chip_phase is not inside status_gutter"
    _, phase_gate = _own_prop(lines, p_start, p_end, p_indent, "opacity")
    assert phase_gate and "phase_offset_active" in phase_gate, (
        "chip_phase must show/hide on root.controller.phase_offset_active.\n"
        f"  found opacity: {phase_gate}"
    )

    # 5. The reference chip does NOT gate itself away. Both answers get
    #    printed; only the emphasis changes.
    r_start, r_end, r_indent = _kv_widget_block(lines, "chip_reference")
    assert g_start <= r_start < g_end, "chip_reference is not inside status_gutter"
    for gate in ("opacity", "disabled", "height", "width"):
        line_no, expr = _own_prop(lines, r_start, r_end, r_indent, gate)
        assert line_no is None or "else 0" not in expr, (
            f"chip_reference must be ALWAYS PRESENT -- 'NO REFERENCE' is an "
            f"answer the operator needs printed, and an indicator that is only "
            f"there when the answer is yes cannot be trusted to be there at "
            f"all. Found a collapse on '{gate}'.\n  {gate}: {expr}"
        )
    _, ref_text = _own_prop(lines, r_start, r_end, r_indent, "text")
    assert ref_text and "thread_ref_latched" in ref_text, (
        "chip_reference's text must switch on root.controller."
        f"thread_ref_latched.\n  found text: {ref_text}"
    )

    # 6. ...and it keeps its THIRD state. In feed mode the reference is still
    #    latched (the firmware clears referenceLatched only on an enable 0->1
    #    edge, and a mode switch never writes enable), so the chip is dimmed,
    #    not hidden: the operator's cue is that his phase reference survives a
    #    turn-feed-turn swap. Drop this binding and feed mode becomes
    #    indistinguishable from threading, which is the claim that matters.
    _, ref_lit = _own_prop(lines, r_start, r_end, r_indent, "lit")
    assert ref_lit and "is_threading" in ref_lit, (
        "chip_reference must dim (lit: False) when the reference is not "
        "currently relevant, driven by root.controller.is_threading. Without "
        "it a latched-but-unused reference in feed mode looks exactly like a "
        "live one.\n  found lit: {}".format(ref_lit)
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
