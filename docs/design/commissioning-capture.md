# Capturing the commissioning values

## The problem

A commissioned Reflex knows things about its lathe that nothing else knows:
backlash, scale ratios, leadscrew calibration, axis names and roles, direction
polarity. Those values are the difference between a controller and a box of
electronics, and they are expensive to recover — some of them take a dial
indicator and an afternoon.

They live in about nineteen YAML files under `REFLEX_CONFIG_DIR`
(`/var/lib/reflex-config` on the machine), one per `SavingDispatcher` instance,
named `<Class>-<id_override>.yaml`. The only writer is `write_settings()` in
`ui/reflex/dispatchers/saving_dispatcher.py`, which **rewrites the whole file**
on any bound property change.

So the card holds exactly one state: the current one. There is no history, no
record of what a value used to be, and nothing that says a value moved at all.

!!! danger "This already happened"
    On **2026-09-07 at 20:01** the machine's commissioning values moved. Nothing
    captured them. The only copy that exists was made by hand, six days later,
    by reading the numbers off the screen. Had the SD card failed in that
    window, the recalibration would simply have been gone.

## The layers

Three layers, built in this order, each useful on its own:

| Layer | State | What it protects against |
|---|---|---|
| **Ledger + snapshots, on the card** | built | a value moving unnoticed; not knowing what it used to be |
| **USB export / import** | next | the card itself dying; moving a configuration to a replacement card |
| **Opt-in cloud sync** | later | the machine and its only backup burning down together |

Only the first layer is built. The second and third are named here because the
document shape below exists to serve all three — a USB export and a cloud sync
that each invented their own directory walk would be two more places for the
capture to be subtly wrong.

### Ledger

`ui/reflex/utils/commissioning_ledger.py` appends to
`<config_dir>/ledger/commissioning.jsonl` — one JSON object per line, per
changed key:

```json
{"ts": "2026-09-13T19:04:11+00:00", "file": "Axis-1", "key": "backlash",
 "old": 0.04, "new": 0.062, "trigger": "backlash", "app": "1.2.0rc3"}
```

JSON Lines rather than YAML because appending is the whole point: a line is
complete the moment it is written, an interrupted write costs one line instead
of the file, and `tail` is a working reader on a machine whose operator has no
terminal. One line per changed key, not per save, so the file reads as a list
of what changed rather than a pile of snapshots to diff by eye. A file written
for the first time contributes one line per commissioning key with `old: null`
— the first write of a dispatcher's file *is* that dispatcher's commissioning
event.

`record()` **never raises into its caller.** An unwritable directory or a full
disk is logged through the Kivy logger and swallowed. The record matters; it
does not matter more than the lathe, and a config save that fails because a
record-keeping directory was not writable would be a worse defect than the one
this closes.

### Snapshots

A ledger says *what moved*. It is not a thing you can restore from. So a
commissioning change also writes the whole configuration to
`<config_dir>/ledger/snapshots/<ts>-change.yaml`.

The app also calls `snapshot_if_changed("startup")` once, from `App.build()`,
after every dispatcher has been constructed and has read its file. That is the
tripwire for **writes the ledger cannot see**: a value edited by hand over SSH,
a file restored from a backup, a card swap. It compares the current
configuration against the newest snapshot (both with `meta` removed, since
`meta.ts` differs on every build) and writes only on a difference.

## The bundle document

`ui/reflex/utils/commissioning_bundle.py` defines the single-document form of
the whole machine configuration. Snapshots are this document; USB export and
cloud sync will carry this document.

```yaml
meta:
  schema: 1
  ts: 2026-09-13T19:04:11+00:00    # UTC, ISO-8601, seconds
  machine_id: 5a2f...              # /etc/machine-id, else platform.node()
  hostname: elspi
  app: 1.2.0rc3                    # installed reflex version, or "unknown"
  fw: 1.2.0                        # passed in by the caller, may be null
Axis-0:                            # one key per YAML stem, verbatim,
  axis_name: C                     # in sorted stem order
  spindleMode: true
Axis-1:
  axis_name: Z
  backlash: 0.04
Device-0:
  use_case: lathe                  # commissioning tier
  current_mode: 2                  # operational tier
Els-0:
  spindle_axis_index: 0
```

`meta` is first in the dumped text so a human opening an export sees what
machine and what moment it came from before anything else.

Bundles exported before 2026-09-16 also carry `config_ini:`, the parsed
`ui/config.ini`, because the machine's `use_case` and `current_mode` still
lived there. Those two keys are now the `Device-0` stem
(`ui/reflex/dispatchers/device.py`; the app migrates them from the ini once, on
the first start with no `Device-0.yaml`, and never reads the ini for them
again). `build()` no longer emits `config_ini`. `apply()` still accepts it: it
logs and ignores the section, except that `config_ini.device.use_case` in a
bundle with no `Device-0` stem is written to `Device-0`, so an old export still
restores a lathe. `meta.schema` stays 1: `apply()` refuses only a newer schema,
and an older app reading a new bundle sees nothing it misreads, just one more
stem (the reasoning is at `SCHEMA` in the module).

`split(doc)` is the inverse of the per-file half and returns `{stem: mapping}`.
**There is no import or apply yet.** Writing a bundle back onto a machine has
its own safety questions — which keys may be overwritten on a machine that is
not the one exported from, what happens to a stem the running app has no
dispatcher for, whether the app must be stopped first — and it is a separate
change.

## The scope contract

Not every persisted key is machine identity. `ui/reflex/utils/commissioning_scope.py`
is the one place that decides, in three tiers:

**`commissioning`** — machine identity. Backlash, calibration constants, gear
and sync ratios, scale resolutions, polarity flags, axis names and roles, the
per-axis transform. **This is the default**: any key not named below is
commissioning, so a new calibration property added to a dispatcher is captured
the day it is added, with nobody remembering to list it.

**`operational`** — job and operator state. The set is deliberately tiny and
exhaustive:

- `offsets` in **any** file — the hundred work offsets, rewritten every time the
  operator zeroes the DRO. This is the highest-frequency write on the machine;
  unfiltered, the ledger would be unreadable.
- `syncRatioNum` / `syncRatioDen` **only** in a file whose data carries
  `spindleMode: true`. On a spindle axis these two are the
  degrees-per-revolution presentation and the ELS bar rewrites them as the
  operator picks a feed. On a **linear** axis the same two keys are the scale
  ratio, which is pure calibration. Same key names, opposite tiers.

**`ignored`** — neither. `id_override` (it is the filename, not a value) and
pure Kivy layout geometry that older save files carry: `size_hint_*`, `spacing`,
`padding`, `pos`/`size`, `x`/`y`/`width`/`height`, `minimum_*`, and the
`natural_height` and `opacity` that `ElsAdvancedBar-*.yaml` still holds on the
machine. A bar that is 158 px tall is not a fact about the lathe.

!!! warning "Spindle-ness is read from the data, never the filename"
    `Axis-0` is the spindle on one lathe and need not be on another — the
    spindle is whichever axis carries `spindleMode: true`, and
    `ElsDispatcher.spindle_axis_index` is operator configuration. A
    filename-based rule would mis-tier the sync ratio on any machine whose axes
    were wired in a different order, silently, and in the direction that loses
    calibration.

!!! danger "Changing a tier is a contract change"
    A separate operations tool computes a field-scoped hash over the
    commissioning tier to answer "has this machine's calibration moved since
    the last snapshot?". That hash is taken over exactly the keys the module
    calls `commissioning`. **Moving an existing key between tiers makes every
    stored hash incomparable** with every hash computed afterwards, and the tool
    reports a machine-wide change that never happened. Adding keys is free —
    unknown keys default to `commissioning`, which is the safe direction — but
    re-tiering an existing key needs the consumer re-baselined in the same
    change.
