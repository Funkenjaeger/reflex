# Reflex

A **digital read-out and electronic leadscrew for manual lathes**: an STM32
motion controller and a Kivy touchscreen, talking RS-485 Modbus RTU.

![The home screen in ELS mode](screenshots/home_els_dark.png)

The firmware owns everything real-time — encoders, step generation, the stop.
The UI owns operator workflow, configuration and display.

!!! warning "This drives a machine tool"
    Reflex can start the carriage moving under power. A refusal is the
    controller declining to cut something it cannot verify, not a fault to work
    around — see [Messages and what to do](guide/when-it-refuses.md).

---

## The one idea to read first

**Reflex will not start a pass it cannot verify.**

Before every pass the controller drives the leadscrew through its backlash and
watches the Z scale to confirm the carriage actually moved. If it did not — an
open half nut, a slipping coupling, a dead scale — the pass does not start.

That check is the point of the machine, not an obstacle in front of it. Earlier
designs had no way to know, so they proceeded and cut the next pass in the wrong
place.

---

## What it does

Five things, and most people use two of them. Each links to its own page; you do
not need to read them in order.

### Three ELS modes

How much of the job the controller takes over. They cut the same thread — what
changes is how much you set up first and how much you do by hand between passes.
Cycle between them with the tri-state tab at the right of the advanced bar.

<div class="grid cards" markdown>

- ![Stop-only](screenshots/home_els_dark.png)

    **Stop-only**

    One field: **Stop Z**. Feed or thread up to a shoulder and stop, hands off.
    Everything else is yours, exactly as on a manual lathe with a carriage stop.

    The least to get wrong, and what the author runs.

    [Feeding to a shoulder →](guide/feeding-to-a-shoulder.md) ·
    [the modes compared →](guide/operator-modes.md#stop-only)

- ![Stop and retract](screenshots/home_els_stopretract.png)

    **Stop + retract**

    Adds **Start Z** and an automatic return. The cycle becomes
    Cut → Retract → Cut instead of Cut → *four things by hand* → Cut.

    This is where phase re-sync earns its keep: you may open the half nut
    between passes.

    [Cutting a thread →](guide/cutting-a-thread.md) ·
    [the modes compared →](guide/operator-modes.md#stop-retract)

- ![The wizard](screenshots/wizard_3_start_dia.png)

    **Wizard**

    Guided setup. Drive to each position and press **Set**; the bar tells you
    what it wants next, and nothing is captured until you press it.

    Useful for a part you have not cut before.

    [Walk the wizard →](guide/operator-modes.md#wizard)

</div>

### Two advanced features

Neither is needed for ordinary threading. Both exist for jobs the basic cycle
cannot express.

<div class="grid cards" markdown>

- ![Picking up an existing thread](screenshots/flow/wt_resync_align.png)

    **Thread phase re-sync** — pick up an existing thread

    Re-establish the thread datum on work this job did not cut: a re-chucked
    part, a thread cut elsewhere, a damaged thread being chased. You show the
    controller where the helix is and it latches a reference at that instant.

    The same mechanism is what lets you open the half nut mid-thread.

    [Read the procedure →](guide/picking-up-a-thread.md)

- ![Widening a groove](screenshots/flow/wt_offset_applied.png)

    **Thread phase offset** — widen a groove

    Shift where the tool enters the helix, so the groove comes out wider than
    the insert cutting it. For an O-ring groove, a clearance thread, or opening
    up a fit after measurement.

    Not a multi-start mechanism — the page says why.

    [Read the procedure →](guide/widening-a-groove.md)

</div>

---

## Where to start

| If you are… | Go to |
|---|---|
| new to the screen | [The screen](guide/the-screen.md) — every region named |
| choosing how to work | [The three ELS modes](guide/operator-modes.md) |
| power feeding to a shoulder | [Feeding to a shoulder](guide/feeding-to-a-shoulder.md) |
| cutting a thread | [Cutting a thread](guide/cutting-a-thread.md) |
| setting up a new machine | [Setup](setup/index.md) — axes, scales, servo, backlash |
| looking at a message | [Messages and what to do](guide/when-it-refuses.md) |
| after the meaning of one field | [Reference](reference/index.md) — the in-app help index |

---

## About the screenshots

Every image in this guide is **generated**, headlessly, at the machine's real
1024×600, by the real widget tree — not photographed and not mocked up. Every
sentence on screen comes out of the same code that draws it on the lathe.

The catalogue of messages is generated too: it is read from the app's own
message tables, so a reworded refusal fails the docs build rather than quietly
leaving this guide wrong.

!!! note "They do not regenerate themselves"
    Nothing in CI rebuilds the images. Run
    `scripts/capture_readme_screenshots.py` and the harnesses under `previews/`
    after a change that alters the home screen, and commit what they write.
