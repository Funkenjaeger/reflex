X DRO Reads
===========

Whether the cross-slide readout shows the **diameter** of the work or the
**radius** — how far the cross slide has physically travelled.

Moving the cross slide 1 mm changes the diameter by 2 mm. On **Diameter**
the readout therefore moves twice as fast as the slide; on **Radius** it
moves with it.

## Where it applies

Only to the axis assigned as **Cross Slide Axis (X)**. It is offered
here, under that assignment, because it is a fact about that role — a
saddle or a spindle has no diameter, so the setting is not offered on
them at all.

If you reassign the X role to a different axis, this follows the role:
it reads whichever axis is X now. The axis you moved away from keeps its
own setting, and its Axis screen says so.

## What it changes, and what it does not

It changes what is **displayed** and what you **type**:

- The X readout shows diameter.
- Typing a position sets the **diameter** — enter 20.000 and the
  readout reads 20.000.
- The ELS diameter fields (**Safe ø**, **Major ø**, **Minor ø**) are
  diameters, as their names say.

It changes nothing about how the machine moves. Zeroing, tool offsets,
the scale ratio and the thread pitch all work in real travel, and the
controller is never told about this setting.

## Set the scale honestly

This exists so the **scale resolution** can be the truth. Without it the
only way to get a diameter readout is to enter a scale twice the real
one, and nothing then records that a doubling was intended — a later
reader sees a 2 µm scale on a 1 µm head and has no way to know which is
wrong.

Enter the resolution printed on the scale, and set this to **Diameter**.

## Notes

- New setups read **Radius**, and changing this does not touch any
  stored scale. If your X scale is currently entered at double the real
  resolution to get a diameter readout, correct the resolution **and**
  switch this to Diameter together — doing only one halves or doubles
  the readout.
- **Inner Thread** decides which direction counts as clear of the work;
  it is independent of this setting.
