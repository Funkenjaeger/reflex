"""The status bar must actually DISPLAY what it polls.

cdb6b4c added StatusBar.cycles_peak, scheduled a 1 Hz poll for it, and
described the cycles cell as showing "cur/peak". The kv label was never
changed. Every layer was individually correct -- the firmware register, the
HAL read, the property, the Clock -- and the number still reached nobody,
because nothing connected the last two. The whole suite passed.

That is the shape this file exists to catch: a value that is fetched, stored,
and never rendered. It reads the kv as text rather than instantiating Kivy,
which needs a window and a GL context that CI does not have.
"""
from pathlib import Path

KV = (Path(__file__).resolve().parents[2]
      / "reflex" / "components" / "home" / "statusbar.kv")


def _kv_text():
    return KV.read_text(encoding="utf-8")


def test_the_cycles_cell_renders_the_peak_not_just_the_current_tick():
    """The peak is the measurement; the current tick is nearly always noise."""
    assert "root.cycles_peak" in _kv_text(), (
        "statusbar.kv never references root.cycles_peak, so the ISR peak is "
        "polled once a second and discarded -- exactly the cdb6b4c defect")


def test_the_cycles_cell_still_shows_the_current_tick():
    assert "root.cycles" in _kv_text()


def test_every_polled_status_property_is_rendered():
    """Generalises the defect instead of pinning the one instance of it.

    Any NumericProperty StatusBar maintains for display should appear in the
    kv. If a future property is added and never wired to a label, this fails
    the way the peak should have."""
    text = _kv_text()
    for prop in ("cycles", "cycles_peak", "fps", "interval"):
        assert f"root.{prop}" in text, (
            f"StatusBar maintains {prop} but statusbar.kv never renders it")
