Stop Re-reference Notice
========================

Sets whether the ELS tells you when a **Stop Z / Start Z** (or a wizard
diameter) that you already set is **re-referenced**. Re-referenced means
that its displayed number changes, because you re-zeroed the DRO or
changed the work-coordinate system after you set it.

The physical target does not move. Stops are anchored to the machine's
encoder position (the actual shoulder). Re-zeroing the DRO or switching
mm/in only changes the label on the displayed number. The tool still
stops at exactly the same physical place. This setting only controls
whether you get an on-screen notice when that label changes.

## Options

- **silent** *(default)*: no notice. The displayed value updates to the
  new reference. Every other position on the DRO does the same when you
  re-zero. Recommended for normal use.
- **warn**: a small amber message shows that the stop/start values were
  re-referenced. Nothing is blocked. The message clears itself when you
  start the next cut or enter a value again.
- **confirm**: an inline bar appears with **Keep** and **Reset**.
  *Keep* closes the notice (the physical targets do not change).
  *Reset* clears Stop Z and Start Z back to "--" so that you enter them
  again. Use it if you typed a literal number (instead of touching off
  the shoulder) and the re-zero changed what that number should mean.

## When it fires

Only on an **offset change**: re-zeroing Z, or switching work coordinate
systems (P0/P1/…). It intentionally does **not** fire on a **mm ↔ in**
switch. Those units are unambiguous (the same physical spot, a different
label). If you typed a literal value, you must reconcile it yourself.

## Which one should I use?

- Touch-off workflow (jog to the shoulder, press Set): **silent** is
  sufficient. The anchor is physical, so a later re-zero does no harm.
- You may type a Stop/Start value as a number, meant against a zero that
  you set afterward. For that case, use **warn** or **confirm**. It
  catches the case, so a re-zero does not leave the target in an
  unexpected place.
