"""Which persisted config keys are MACHINE IDENTITY and which are job state.

THIS MODULE IS A CONTRACT, NOT A CONVENIENCE. A separate operations tool
computes a field-scoped hash over the commissioning tier to answer "has this
machine's calibration moved since the last snapshot?". That hash is taken over
exactly the keys :func:`tier` calls ``"commissioning"``. So MOVING A KEY
BETWEEN TIERS IS A CONTRACT CHANGE: every stored hash computed before the move
becomes incomparable with every hash computed after it, and the tool reports a
machine-wide change that never happened. Add keys freely -- an unknown key
defaults to ``commissioning``, which is the safe direction -- but re-tiering an
existing key needs the consumer re-baselined in the same change.

THE THREE TIERS
---------------
``commissioning``
    Machine identity: backlash, calibration constants, gear and sync ratios,
    scale resolutions, polarity/direction flags, axis names and roles, the
    per-axis transform, and the machine's ``use_case`` in ``Device-0.yaml``
    (lathe / rotary table: it decides which modes exist at all). These are
    what a card death loses and what a
    replacement card has to be told. Everything not named below is this, by
    default and on purpose -- a new calibration property added to a dispatcher
    is captured the day it is added, with nobody remembering to list it here.

``operational``
    Job and operator state, which moves constantly during normal work and
    carries no information about the machine itself. The set is deliberately
    TINY and exhaustive:

    * ``offsets`` in ANY file -- the 100 work offsets, rewritten every time
      the operator zeroes the DRO.
    * ``current_mode`` in ANY file -- the operating mode last selected on the
      home screen (``Device-0.yaml``). Added 2026-09-16 with that stem; no
      stem carried the key before, so this is an addition, not a re-tier.
    * ``offer_prereleases`` in ANY file -- the Software Update screen's
      "Offer pre-releases" toggle (``Device-0.yaml``). An operator preference
      about which releases to be shown, not a fact about the machine. Added
      2026-09-19 with the key itself.
    * ``feed_name`` / ``current_feeds_index`` in ANY file -- the feed picked
      on the ELS bar (``ElsBar-0.yaml``). Re-tiered 2026-09-19: as the
      commissioning default, every feed pick was ledgered as a machine change
      (10 of the lathe's 13 ledger lines as of that day), and each would be a
      gist revision once auto-sync worked.
    * ``syncRatioNum`` / ``syncRatioDen`` ONLY in a file whose data carries
      ``spindleMode: true``. On a spindle axis these two are the
      degrees-per-revolution presentation of the encoder and the ELS bar
      rewrites them as the operator picks a feed (see
      ``components/home/elsbar.py``); on a LINEAR axis the same two keys are
      the scale ratio, which is pure calibration. Same key names, opposite
      tiers, and the only thing that separates them is what the axis is.

``ignored``
    Neither identity nor job state: noise that should appear in neither the
    ledger nor a commissioning diff.

    * ``id_override`` -- it is the filename, not a value (see the long comment
      in ``dispatchers/saving_dispatcher.read_settings``); it is written for
      legibility only and is never an input.
    * Pure Kivy layout geometry that older save files carry because
      ``SavingDispatcher`` persists every Numeric/String/Boolean property it is
      not told to skip: ``size_hint_*``, ``spacing``, ``padding``, ``pos``,
      ``size``, ``x``/``y``/``width``/``height``, ``minimum_*``, ``position``,
      and the two that are documented as actually present on the machine --
      ``natural_height`` and ``opacity`` in ``ElsAdvancedBar-*.yaml`` (see the
      ``_skip_save`` comment in ``components/home/els_advbar.py``). A bar that
      is 158 px tall is not a fact about the lathe.

SPINDLE-NESS IS READ FROM THE DATA, NEVER FROM THE FILENAME. ``Axis-0`` is not
the spindle on every machine -- the spindle is whichever axis has
``spindleMode: true``, and ``ElsDispatcher.spindle_axis_index`` is operator
configuration. A filename rule would mis-tier the sync ratio on any machine
whose axes are wired in a different order, silently, and in the direction that
loses calibration.
"""
from typing import Literal

Tier = Literal["commissioning", "operational", "ignored"]

COMMISSIONING: Tier = "commissioning"
OPERATIONAL: Tier = "operational"
IGNORED: Tier = "ignored"

#: Operational in every file, no matter what the file is.
OPERATIONAL_KEYS = frozenset({"offsets", "current_mode", "offer_prereleases",
                              "feed_name", "current_feeds_index"})

#: Operational only when the file's own data says the axis is the spindle.
SPINDLE_ONLY_OPERATIONAL_KEYS = frozenset({"syncRatioNum", "syncRatioDen"})

#: The flag that makes the two keys above operational. Read from the data.
SPINDLE_FLAG = "spindleMode"

#: Keys that are neither tier. See the module docstring for each one's reason.
IGNORED_KEYS = frozenset({
    "id_override",
    # ── pure Kivy layout geometry ────────────────────────────────────
    "spacing", "padding",
    "pos", "size",
    "x", "y", "width", "height",
    "minimum_width", "minimum_height",
    "position",
    "natural_height", "opacity",
})

#: Prefixes for layout keys that come in families (``size_hint_x``,
#: ``size_hint_y``, ``size_hint_min_x``, ...). A prefix rather than a literal
#: list so a new member of the family is ignored without an edit here.
IGNORED_KEY_PREFIXES = ("size_hint",)


def tier(file_stem: str, key: str, data: dict) -> Tier:
    """Classify one key of one config file.

    :param file_stem: the save file's stem, e.g. ``"Els-0"`` or ``"Axis-2"``.
        Carried for provenance in log lines and for any future per-file rule.
        It is deliberately NOT consulted to decide spindle-ness -- see the
        module docstring.
    :param key: the key inside that file's mapping.
    :param data: the file's whole mapping, which is what
        :data:`SPINDLE_FLAG` is read from. May be empty; an absent flag is
        falsy, so a linear axis's sync ratio stays commissioning.
    """
    if key in IGNORED_KEYS or key.startswith(IGNORED_KEY_PREFIXES):
        return IGNORED
    if key in OPERATIONAL_KEYS:
        return OPERATIONAL
    if key in SPINDLE_ONLY_OPERATIONAL_KEYS and (data or {}).get(SPINDLE_FLAG) is True:
        return OPERATIONAL
    return COMMISSIONING
