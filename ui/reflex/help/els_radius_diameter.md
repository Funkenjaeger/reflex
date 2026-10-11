X DRO Reads
===========

This sets whether the cross-slide readout shows the **diameter** of the
work or the **radius**. The radius is how far the cross slide has
physically traveled.

A 1 mm move of the cross slide changes the diameter by 2 mm. So on
**Diameter**, the readout moves twice as fast as the slide. On **Radius**,
it moves with the slide.

## Where it applies

This applies only to the axis assigned as **Cross Slide Axis (X)**. It is
on this screen, under that assignment, because it is a fact about that
role. A saddle or a spindle has no diameter, so the setting is not offered
for them at all.

If you assign the X role to a different axis, this setting follows the
role. It reads whichever axis is X now. The axis that was X before keeps
its own setting, and its Axis screen says so.

## What it changes, and what it does not

It changes what is **displayed** and what you **type**:

- The X readout shows diameter.
- Typing a position sets the **diameter**. Enter 20.000 and the readout
  shows 20.000.
- The ELS diameter fields (**Safe ø**, **Major ø**, **Minor ø**) are
  diameters, as their names say.

It changes nothing about how the machine moves. Zeroing, tool offsets,
the scale ratio and the thread pitch all work in real travel. The
controller is never told about this setting.

## Set the scale honestly

This setting exists so that the **scale resolution** can be the true
value. Without it, the only way to get a diameter readout is to enter a
scale twice the real one. Then nothing records that the doubling was
intentional. A later reader sees a 2 µm scale on a 1 µm head and cannot
know which one is wrong.

Enter the resolution printed on the scale. Set this to **Diameter**.

## Notes

- New setups read **Radius**. A change to this setting does not change
  any stored scale. Your X scale may be entered at double the real
  resolution to get a diameter readout. If so, correct the resolution
  **and** change this to Diameter at the same time. If you do only one of
  the two, the readout is halved or doubled.
- **Inner Thread** sets which direction counts as clear of the work. It is
  independent of this setting.
