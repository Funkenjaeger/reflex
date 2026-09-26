# Stop overshoot correction

!!! warning "Experimental, and off by default"
    This has been measured and tested on one machine only, the development
    lathe, and there is no calibration wizard yet. On any other lathe, leave it
    off, or turn it on knowing that the correction may be the wrong size for
    your machine. Check it as described below before you trust it.

## What it corrects

When the carriage reaches **Stop Z**, the firmware stops sending steps on time.
The carriage does not stop on time. The servo drive has latency: it keeps
driving the leadscrew for a moment after the last step, so the carriage
overshoots the stop. The faster the feed, the further it overshoots.

On the development lathe that is nothing when hand-cranked, 7 counts (35 µm)
at .040 in/rev and about 340 rpm, and a little over 0.2 mm at the fastest feed
measured. It is the same every time at a given speed, which is what makes it
correctable. When you are turning to a shoulder, all of it goes into the
shoulder.

This is a limitation of the drive, not of the stop. The correction works
around it.

## What it does

With the correction on, the UI watches how fast Z is approaching and asks the
firmware to fire the stop **early**, by the overshoot measured for that speed
plus a small margin. The carriage comes to rest just **short** of Stop Z instead
of just past it. Your Stop Z itself never changes.

## Where to find it

Tap the **gear** on the advanced ELS bar, under the mode selector, to open
**ELS Threading Settings**. The two settings are at the bottom, under
**Advanced**:

| Setting | What it does |
|---|---|
| **Stop overshoot correction** | On or off. **Default: off.** |
| **Overshoot margin (Z counts)** | Extra counts of early stop on top of the measured overshoot. **Default: 1**, the value it was sized for. Larger values stop further short. 0 aims at the worst overshoot measured, with nothing to spare. |

## Is it the right size for your machine?

The correction is a table measured on the development lathe, with:

- a Z scale of 5 µm per count (the table is kept in encoder counts);
- that lathe's servo drive and the drive's own settings;
- that lathe's gearing between the motor and the carriage: the gearbox
  position and the leadscrew.

The overshoot is the drive carrying the carriage on after the stop fires: a
few milliseconds at full speed, then a slow-down. The slow-down is set by the
drive and by the gearing, so either one can change the size of the overshoot.
The **Maximum Speed** and **Acceleration** settings in **Setup → Servo** do not
enter into it: by the time the overshoot happens, the controller has already
stopped sending steps.

If your machine differs in any of the three, the table does not describe it,
and the correction can be too small. If you change the gearbox position, the
drive's settings or the Z scale on a machine where it was working, turn it off
until you have checked it again.

## Checking it on your lathe

Do this at each feed you intend to use, because the overshoot grows with speed.

1. **Air first.** Keep the tool clear of the work. Set a Stop Z, engage, and
   [feed to it](feeding-to-a-shoulder.md) with the correction **off**. When the
   carriage has settled, compare the Z DRO with Stop Z. The difference is your
   overshoot.
2. **Turn it on** and make a few more passes at the same feed. The Z DRO should
   settle **at Stop Z or a hair short** of it, never past. In real cuts to a
   shoulder on the development lathe, 9 stops out of 9 landed 1 to 4 counts
   (5 to 20 µm) short.
3. **Then cut a real shoulder** and check it.

**If any stop lands past Stop Z, turn it off.** Past means further in the
direction the carriage was travelling.

## Faster than it was measured

The table stops at about 3,500 counts per second of Z travel, roughly 40 in/min
on a 5 µm scale. Faster than that, the correction holds at its largest value
and says so once per pass:

> Z rate above the calibrated range (3529 counts/s); correction held at its top value

The carriage can then overshoot further than the correction allows for, so
check those stops by eye.
