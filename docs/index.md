# Reflex

A **digital read-out and electronic leadscrew for manual lathes**: an STM32
motion controller and a Kivy touchscreen, talking RS-485 Modbus RTU.

![The home screen in ELS mode](screenshots/home_els_dark.png)

The firmware owns everything real-time — encoders, step generation, the stop.
The UI owns operator workflow, configuration and display. The wire between them
is a memory-mapped register contract guarded by a protocol version.

!!! warning "This drives a machine tool"
    Reflex can start the carriage moving under power. Every procedure in this
    guide assumes you have read [When it refuses](guide/when-it-refuses.md) and
    understand that a refusal is the controller declining to cut, not a fault to
    work around.

## Where to start

<div class="grid cards" markdown>

- **New to the machine**

    [The screen](guide/the-screen.md) names every region, then
    [The three operator modes](guide/operator-modes.md) explains how much of the
    job the controller takes over.

- **You want to cut something**

    [Feeding to a shoulder](guide/feeding-to-a-shoulder.md) is the simplest
    useful thing it does. [Cutting a thread](guide/cutting-a-thread.md) is the
    reason it exists.

- **You have a part already in the chuck**

    [Picking up an existing thread](guide/picking-up-a-thread.md) re-establishes
    the thread reference on work that has been out of the machine.

- **You just built one**

    [Commissioning](guide/commissioning.md) walks the axes, scales, servo
    gearing and the backlash calibration.

</div>

## What the screenshots are

Every image in this guide is **generated**, headlessly, at the machine's real
1024×600, by the real widget tree — not photographed and not mocked up. Nothing
is typed into them: every sentence on screen comes out of the same code that
draws it on the lathe, and every number is arithmetic the app did over its own
live geometry. The hardware is stubbed at exactly one layer, so everything above
it is the real thing refusing a real entry.

That is deliberate. A user guide illustrated by hand drifts from the software
silently; this one drifts loudly, because the generators are also the regression
checks for the screens they photograph.

!!! note "They do not regenerate themselves"
    Nothing in CI rebuilds these. Run
    `scripts/capture_readme_screenshots.py` and the harnesses under
    `previews/` after a change that alters the home screen, and commit what they
    write.
