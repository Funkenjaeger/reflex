# The screen

![The home screen, ELS mode, stop-only](../screenshots/home_els_dark.png)

The display is 1024×600 and everything on it is reachable by thumb. It divides
into five regions.

## 1 — The sidebar

Down the left edge, from the top:

| Control | What it does |
|---|---|
| **MM / IN** | Display units. Switching re-renders every readout; it does not move anything. |
| **P0 / P1 / P2 / P3** | Four work-offset slots. Zeroing an axis zeroes it in the selected slot, so you can keep several datums for one job. |
| **ABS / INC** | Absolute or incremental readout. |
| **Wand** | Opens the pattern screen (hole circles, lines, rectangles). Hidden unless patterns are enabled. |
| **ELS / DRO** | Operating mode. `DRO` is a plain read-out; `ELS` adds the leadscrew. Tapping cycles. |
| **Gear** | Settings. |

The mode selector only offers what the machine's *use case* allows — a lathe
exposes ELS, other use cases do not, and asking for a mode that is not allowed
silently falls back to DRO rather than half-starting one.

## 2 — The status bar

Across the top: the link indicator (`COM`), the live link counters, the firmware
version and the logo. `COM` lit means the UI and the controller are talking —
if it is dark, nothing below it is live and every number on screen is stale.

## 3 — The DRO

The large seven-segment readouts: **Z**, **X**, and spindle **RPM**, each with a
feed rate beneath it in distance per minute. The `Zero` buttons zero that axis in
the current work offset — not in the machine frame, and not in the controller.

## 4 — The status gutter

![The gutter with a reference latched and an offset applied](../screenshots/flow/wt_gutter_on.png)

A permanently reserved 26-pixel strip between the DRO and the controls. It holds
two chips and never covers anything else:

**Left — the thread reference.** Reads `NO REFERENCE` or `REF LATCHED`. This is
the datum the controller cuts threads against. It persists across a switch to
feed mode — the chip dims rather than disappearing, because the reference is
still there and still usable.

**Right — the phase offset.** Present only when a thread-phase offset is set,
showing the distance and its share of one pitch. See
[Widening a groove](widening-a-groove.md).

When the controller has something to say, a translucent notice strip lands
across this gutter. It is sized so it never lands on top of either chip's text.

## 5 — The advanced bar

The control row, and its contents depend on the mode
([see the next page](operator-modes.md)). In stop-only, from the left:

- **Engage / Disengage** with its own LED — `Disabled`, `Armed`, or a fault.
- **Stop Z** — the shoulder you are feeding to.
- **Cut** — the action button. It is blank and recessed when there is no action
  available, and lights up when there is.
- **Mode selector and settings** on the right.

Below that: **Sync Enable**, the **DIR** direction pair, **ADV** (expands the
bar), the thread-hand illustration, and the feed/thread selector with the
current pitch.

!!! tip "The action button always says what it will do"
    It reads `Cut`, `Retract`, `Set` or `Confirm` depending on where you are.
    If it is blank, the controller has nothing for you to press yet — and the
    instruction line above the fields usually says what it is waiting for.
