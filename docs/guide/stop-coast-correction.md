# Stop coast correction

!!! warning "Experimental, and off by default"
    This has been measured and tested on one machine only, the development
    lathe, and there is no calibration wizard yet. On any other lathe, leave it
    off, or turn it on knowing that the correction may be the wrong size for
    your machine. Check it as described below before you trust it.

## What it corrects

When the carriage reaches **Stop Z**, the firmware stops sending steps on time.
The carriage does not stop on time. The servo drive has latency, so it keeps
moving for a moment after the last step, and the carriage coasts past the
stop. The faster the feed, the further it coasts.

On the development lathe that is nothing when hand-cranked, 7 counts (35 µm)
at .040 in/rev and about 340 rpm, and a little over 0.2 mm at the fastest feed
measured. It is the same every time at a given speed, which is what makes it
correctable. When you are turning to a shoulder, all of it goes into the
shoulder.

This is a limitation of the drive, not of the stop. The correction works
around it.

## What it does

With the correction on, the UI watches how fast Z is approaching and asks the
firmware to fire the stop **early**, by the coast measured for that speed plus
a small margin. The carriage comes to rest just **short** of Stop Z instead of
just past it. Your Stop Z itself never changes.

## Where to find it

Tap the **gear** on the advanced ELS bar, under the mode selector, to open
**ELS Threading Settings**. The two settings are at the bottom, under
**Advanced**:

| Setting | What it does |
|---|---|
| **Stop coast correction** | On or off. **Default: off.** |
| **Coast margin (Z counts)** | Extra counts of early stop on top of the measured coast. **Default: 1**, the value it was sized for. Larger values stop further short. 0 aims at the worst coast measured, with nothing to spare. |

## Is it the right size for your machine?

The correction is a table measured on the development lathe, in this
configuration:

- a Z scale of 5 µm per count;
- in **Setup → Servo**, **Maximum Speed (Steps/s)** 10000 and
  **Acceleration (Steps/s^2)** 20000;
- that lathe's servo drive.

The coast comes from the drive and those settings. If your machine differs in
any of them, the table does not describe it, and the correction can be too
small. If you change any of them on a machine where it was working, turn it off
until you have checked it again.

## Checking it on your lathe

Do this at each feed you intend to use, because the coast grows with speed.

1. **Air first.** Keep the tool clear of the work. Set a Stop Z, engage, and
   [feed to it](feeding-to-a-shoulder.md) with the correction **off**. When the
   carriage has settled, compare the Z DRO with Stop Z. The difference is your
   coast.
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

The carriage can then coast further than the correction allows for, so check
those stops by eye.
