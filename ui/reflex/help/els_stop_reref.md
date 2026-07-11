Stop Re-reference Notice
========================

Controls whether the ELS flags you when a **Stop Z / Start Z** (or a
wizard diameter) you already set gets **re-referenced** — that is, its
displayed number changes because you re-zeroed the DRO or switched
work-coordinate system after setting it.

The physical target never moves. Stops are anchored to the machine's
encoder position (the actual shoulder), so re-zeroing the DRO or
switching mm/in only re-labels the displayed number — the tool still
stops at exactly the same physical place. This setting is purely about
whether you get an on-screen heads-up when that re-labeling happens.

## Options

- **silent** *(default)* — no notice. The displayed value just updates
  to the new reference, the same way every other position on the DRO
  reframes when you re-zero. Recommended for normal use.
- **warn** — a small amber message appears noting that the stop/start
  values were re-referenced. Nothing is blocked; the flag clears itself
  when you start the next cut or re-enter a value.
- **confirm** — an inline bar appears with **Keep** and **Reset both**.
  *Keep* dismisses the notice (the physical targets are unchanged).
  *Reset both* clears Stop Z and Start Z back to "--" so you re-enter
  them — use this if you had typed a literal number (rather than
  touching off the shoulder) and the re-zero changed what that number
  should mean.

## When it fires

Only on an **offset change** — re-zeroing Z, or switching work
coordinate systems (P0/P1/…). It deliberately does **not** fire on a
**mm ↔ in** switch: those units are unambiguous (the same physical spot,
different label), so a literal value you typed is on you to reconcile.

## Which one should I use?

- Touch-off workflow (jog to the shoulder, press Set): **silent** is
  fine — the anchor is physical, so a later re-zero is harmless.
- If you sometimes type a Stop/Start value as a number intending it
  against a zero you set afterward: **warn** or **confirm** catches that
  corner case so a re-zero doesn't leave the target where you didn't
  expect.
