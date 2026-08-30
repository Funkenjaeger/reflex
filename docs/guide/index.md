# User guide

This guide is organised by **what you are trying to do**, not by what the
software contains. If you want the meaning of one particular field, that lives
in the machine's own help — press the **?** on any settings screen — and is
indexed under [Reference](../reference/index.md).

## The shape of it

| Page | What it covers |
|---|---|
| [The screen](the-screen.md) | Every region of the display, named. |
| [The three operator modes](operator-modes.md) | Stop-only, stop + retract, wizard — and how much each takes over. |
| [Feeding to a shoulder](feeding-to-a-shoulder.md) | Power feeding onto an electronic stop. |
| [Cutting a thread](cutting-a-thread.md) | Single-point threading, pass after pass. |
| [Picking up an existing thread](picking-up-a-thread.md) | Re-establishing phase on work that left the machine. |
| [Widening a groove](widening-a-groove.md) | Shifting thread phase deliberately. |
| [When it refuses](when-it-refuses.md) | Every message that stops a cut, and what to do about it. |
| [Commissioning](commissioning.md) | First setup: axes, scales, servo gearing, backlash. |

## One idea worth reading first

Reflex will refuse to start a pass it cannot verify.

Before every threading pass the controller drives the leadscrew through its
backlash and **watches the Z scale to confirm the carriage actually moved**. If
it did not — an open half nut, a slipping coupling, a disconnected scale — the
pass does not start, and you get a message saying so.

That check is the point of the machine, not an obstacle in front of it. Earlier
versions had no way to know, so they would proceed and cut the next pass in the
wrong place. When you see a refusal, the controller has just prevented that.
