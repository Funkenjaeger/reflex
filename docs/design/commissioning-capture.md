# Capturing the commissioning values

## The problem

A commissioned Reflex knows things about its lathe that nothing else knows:
backlash, scale ratios, leadscrew calibration, axis names and roles, direction
polarity. Some of them take a dial indicator and an afternoon to recover.

They live in about nineteen YAML files under `REFLEX_CONFIG_DIR`
(`/var/lib/reflex-config` on the machine), one per `SavingDispatcher` instance,
named `<Class>-<id_override>.yaml`. The only writer is `write_settings()` in
`ui/reflex/dispatchers/saving_dispatcher.py`, which **rewrites the whole file**
on any bound property change. So the card holds only the current state, with no
history.

## The layers

Three layers, each useful on its own:

| Layer | What it protects against |
|---|---|
| **Ledger + snapshots, on the card** | a value moving unnoticed; not knowing what it used to be |
| **USB export / import** | the card itself dying; moving a configuration to a replacement card |
| **Opt-in cloud sync** | the machine and its only backup burning down together |

All three carry the bundle document below, so there is one directory walk.

### Ledger

`ui/reflex/utils/commissioning_ledger.py` appends to
`<config_dir>/ledger/commissioning.jsonl`, one JSON object per line, per
changed key:

```json
{"ts": "2026-09-13T19:04:11+00:00", "file": "Axis-1", "key": "backlash",
 "old": 0.04, "new": 0.062, "trigger": "backlash", "app": "1.2.0rc3"}
```

JSON Lines, so an interrupted write costs one line, not the file. One line per
changed key, not per save. A file's first write contributes one line per
commissioning key with `old: null`.

`record()` **never raises into its caller.** An unwritable directory or a full
disk is logged through the Kivy logger and swallowed. A failed record must
never fail a config save.

### Snapshots

A ledger says *what moved*; it cannot be restored from. So a commissioning
change also writes the whole configuration to
`<config_dir>/ledger/snapshots/<ts>-change.yaml`.

Once every dispatcher has read its file, `App.build()` calls
`snapshot_if_changed("startup")`, the tripwire for **writes the ledger cannot
see**: a hand edit over SSH, a restored file, a card swap. It writes only when
the configuration differs from the newest snapshot, `meta` aside.

## The bundle document

`ui/reflex/utils/commissioning_bundle.py` defines the single-document form of
the whole machine configuration. Snapshots, USB export and cloud sync all carry
this document.

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

`use_case` and `current_mode` are the `Device-0` stem
(`ui/reflex/dispatchers/device.py`). Older bundles carry them in a
`config_ini:` section, which `apply()` logs and ignores, except that in a
bundle with no `Device-0` stem `config_ini.device.use_case` is written to
`Device-0`, so an old export still restores a lathe. `meta.schema` is 1 (the
reasoning is at `SCHEMA` in the module).

`split(doc)` is the inverse of the per-file half and returns `{stem: mapping}`.
`apply(doc, config_dir)` is the inverse: it writes every stem back verbatim,
refuses a bundle whose `meta.schema` is newer than the app knows, refuses a
section only when none of its keys is commissioning-tier, and writes each file
atomically. It is what the Backup screen's USB import calls.

## The scope contract

Not every persisted key is machine identity. `ui/reflex/utils/commissioning_scope.py`
is the one place that decides, in three tiers:

**`commissioning`**: machine identity. Backlash, calibration constants, gear
and sync ratios, scale resolutions, polarity flags, axis names and roles, the
per-axis transform. **This is the default**: any key not named below is
commissioning, so a new calibration property is captured without anyone
listing it.

**`operational`**: job and operator state. The set is deliberately tiny and
exhaustive:

- `offsets` in **any** file: the hundred work offsets, rewritten every time the
  operator zeroes the DRO, the highest-frequency write on the machine.
- `syncRatioNum` / `syncRatioDen` **only** in a file whose data carries
  `spindleMode: true`. On a spindle axis these two are the
  degrees-per-revolution presentation and the ELS bar rewrites them as the
  operator picks a feed. On a **linear** axis the same two keys are the scale
  ratio, which is pure calibration.

**`ignored`**: neither. `id_override` (it is the filename, not a value) and
pure Kivy layout geometry that older save files carry: `size_hint_*`, `spacing`,
`padding`, `pos`/`size`, `x`/`y`/`width`/`height`, `minimum_*`, and
`natural_height` and `opacity` in `ElsAdvancedBar-*.yaml`.

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
