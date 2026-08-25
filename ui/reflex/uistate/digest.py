"""Drift guard -- an independent digest of what was actually on screen.

WHY THIS EXISTS. Semantic replay fails *silently*. A visual driven by state the
schema does not declare reproduces at its default, and the screenshot is
confident and wrong -- worse than no screenshot, because nobody doubts it. This
walks the rendered widget tree at capture time and again at replay, and shouts
when the two disagree.

IT MUST STAY INDEPENDENT OF THE SCHEMA. Hashing the declared field values
instead would be circular: it would prove the fields round-trip, never that they
COVER the screen. The whole value of this digest is that it observes something
the schema did not produce.

WHICH IS ALSO WHY IT CANNOT BE THE CAPTURE. What it reads -- `pos`, `size`,
resolved `text`, resolved `color` -- are OUTPUTS of layout and kv bindings.
They cannot be pushed back: the next layout pass recomputes geometry from
`size_hint` and sibling content, and the next binding fire overwrites resolved
text. Replaying from them would need a second renderer running in parallel to
the real kv, which is exactly the drift this guard exists to detect. Inputs are
captured (`schema_v1.py`); outputs are hashed here.

PER-SUBTREE, NOT ONE NUMBER. The walk is happening anyway, so it costs nothing
extra to digest each major region separately. Roughly 40 bytes on a ~300-byte
code buys a drift report that says *the ELS advanced bar* rather than
*something, somewhere* -- the difference between knowing a bug exists and
knowing where to look.

VISIBILITY IS TRACKED BY CONSTRUCTION. This UI hides things by collapsing them
to zero height and zero opacity (``els_advbar.kv``: ``height: dp(30) if
root.controller.takeup_warning else 0``), so skipping zero-sized and
zero-opacity subtrees makes the digest follow what is actually visible without
any special-casing.
"""

import hashlib

from kivy.logger import Logger

log = Logger.getChild(__name__)

# Widgets whose subtree gets its own digest. Names rather than imported classes:
# importing widget modules here would drag the whole component tree into a
# module the recorder loads at startup, and a name comparison cannot fail on a
# refactor in a way that silently disables the guard -- a renamed anchor just
# folds into its parent region, which the digest then reports as changed.
ANCHORS = (
    "HomeToolbar",
    "StatusBar",
    "CoordBar",
    "DroCoordBar",
    "ElsSpindleInfo",
    "ElsBar",
    "ElsAdvancedBar",
    "JogBar",
    "ServoBar",
)

# Geometry is rounded before hashing: sub-pixel differences between the Pi's GL
# and a dev box's are not drift, and hashing raw floats would report them as if
# they were.
_ROUND = 1


def _visible(widget) -> bool:
    if getattr(widget, "opacity", 1) == 0:
        return False
    width = getattr(widget, "width", 1)
    height = getattr(widget, "height", 1)
    return width > 0 and height > 0


def _describe(widget) -> str:
    """The rendered facts about one widget, as a stable string."""
    parts = [type(widget).__name__]
    for name in ("x", "y", "width", "height"):
        value = getattr(widget, name, None)
        if value is not None:
            parts.append(f"{name}={round(float(value), _ROUND)}")
    for name in ("opacity", "disabled"):
        value = getattr(widget, name, None)
        if value is not None:
            parts.append(f"{name}={value}")
    for name in ("text", "font_name", "label"):
        value = getattr(widget, name, None)
        if isinstance(value, str) and value:
            parts.append(f"{name}={value}")
    for name in ("color", "background_color"):
        value = getattr(widget, name, None)
        if value is not None and not isinstance(value, str):
            try:
                parts.append(f"{name}=" + ",".join(
                    f"{round(float(c), 3)}" for c in list(value)[:4]))
            except (TypeError, ValueError):
                pass
    return "|".join(parts)


def walk(widget, region: str = "root"):
    """Yield ``(region, widget)`` for every visible widget under ``widget``.

    One traversal, so callers that want both the digest and a verbose dump pay
    for it once.
    """
    if widget is None or not _visible(widget):
        return
    name = type(widget).__name__
    if name in ANCHORS:
        region = name
    yield region, widget
    for child in getattr(widget, "children", ()):
        yield from walk(child, region)


def _open_modals():
    """Open ``ModalView``s, which live on the Window rather than under root."""
    try:
        from kivy.core.window import Window
        from kivy.uix.modalview import ModalView
    except Exception:  # noqa: BLE001 - no Window in some headless contexts
        return []
    return [w for w in getattr(Window, "children", ())
            if isinstance(w, ModalView)]


def subtree_digests(app) -> dict[str, int]:
    """Digest every visible region of the current screen.

    Returns region name -> 32-bit digest, plus ``"all"`` over the whole set.
    Never raises: a guard that can take the UI down is worse than no guard.
    """
    accumulators: dict[str, "hashlib._Hash"] = {}

    def account(region, widget):
        acc = accumulators.get(region)
        if acc is None:
            acc = accumulators[region] = hashlib.blake2b(digest_size=4)
        acc.update(_describe(widget).encode("utf-8", "replace"))

    try:
        for region, widget in walk(getattr(app, "root", None)):
            account(region, widget)
        for index, modal in enumerate(_open_modals()):
            for _region, widget in walk(modal, f"modal{index}"):
                account(f"modal{index}", widget)
    except Exception as e:  # noqa: BLE001 - never break capture over a digest
        log.debug(f"uistate: widget walk failed ({e}); digest will be partial")

    digests = {region: int.from_bytes(acc.digest(), "big")
               for region, acc in accumulators.items()}

    combined = hashlib.blake2b(digest_size=4)
    for region in sorted(digests):
        combined.update(f"{region}:{digests[region]}".encode())
    digests["all"] = int.from_bytes(combined.digest(), "big")
    return digests


def describe_tree(app) -> list[str]:
    """Verbose per-widget dump, for chasing down a drift report.

    Opt-in only (``REFLEX_UISTATE_VERBOSE``): it is a debugging aid, never a
    replay source, for the reasons in this module's docstring.
    """
    lines = []
    for region, widget in walk(getattr(app, "root", None)):
        lines.append(f"{region}\t{_describe(widget)}")
    for index, modal in enumerate(_open_modals()):
        for _region, widget in walk(modal, f"modal{index}"):
            lines.append(f"modal{index}\t{_describe(widget)}")
    return lines


def compare(recorded: dict[str, int], observed: dict[str, int]) -> list[str]:
    """Regions that differ, or appeared/vanished. Empty means a faithful replay."""
    drifted = []
    for region in sorted(set(recorded) | set(observed)):
        if region == "all":
            continue
        if recorded.get(region) != observed.get(region):
            if region not in recorded:
                drifted.append(f"{region} (not in capture)")
            elif region not in observed:
                drifted.append(f"{region} (missing at replay)")
            else:
                drifted.append(region)
    return drifted
