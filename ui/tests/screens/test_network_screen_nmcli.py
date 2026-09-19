"""NetworkScreen survives an nmcli that is present and REFUSES.

THE FAILURE THESE PIN, found on the lathe 2026-09-13 on the first card where
the UI runs as an unprivileged service user: ``__init__`` did
``self.wifi_enabled = nmcli.radio().wifi``, that property write dispatched
``on_wifi_enabled``, and the handler called ``nmcli.radio.wifi_on()``
unguarded. polkit refused ("Not authorized to perform this operation", as
``nmcli._exception.UnspecifiedException``), the exception propagated out of
``Manager.__init__`` and ``App.build()`` died. A machine controller that could
not manage wifi would not start at all.

So the assertions here are about the SHAPE of construction, not about wifi
working: constructing the screen never raises, and construction never issues a
radio command. ``test_construction_never_commands_the_radio`` is the one that
would have caught the original defect -- and it is asserted on ``wifi_on`` not
being CALLED, rather than on the constructor not raising, because a
``wifi_on`` that happened to succeed for root hid this bug for the entire life
of the screen up to that card.

Construction is driven with ``apply_class_lang_rules`` stubbed, the same
pattern as ``tests/screens/test_update_screen.py``: building this screen's real
kv tree under the mock GL backend segfaults (verified, exit 139 -- the mock
backend cannot service the real child widgets' graphics instructions). The stub
populates the one id ``__init__`` reaches for. The headless Window and the
mock backends come from the repo-root ``ui/conftest.py``.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import reflex.components.screens.network_screen as ns


class Refused(Exception):
    """Stands in for nmcli._exception.UnspecifiedException.

    The real one is what polkit's "Not authorized to perform this operation"
    arrives as. Not imported, deliberately: the production code must catch
    ANY nmcli failure, so binding this suite to one exception class would let
    a narrower `except` pass.
    """


def _stub_rules(self, *args, **kwargs):
    """Stand in for Widget.apply_class_lang_rules.

    ``NetworkScreen.__init__`` binds ``grid_layout``'s minimum_height, so the
    id has to exist; nothing else in the constructor touches the tree.
    """
    self.ids['grid_layout'] = MagicMock(name="grid_layout")


@pytest.fixture
def nmcli(monkeypatch):
    """Replace the `nmcli` module object that network_screen imported.

    Every call site in the module under test resolves through this global, so
    this is the one seam. ``radio()`` reports wifi on by default -- the value
    that made the old constructor dispatch a state CHANGE (False -> True) and
    so reach wifi_on.
    """
    fake = MagicMock(name="nmcli")
    fake.radio.return_value = SimpleNamespace(wifi=True)
    monkeypatch.setattr(ns, "nmcli", fake)
    monkeypatch.setattr(ns, "NMCLI_AVAILABLE", True)
    monkeypatch.setattr(ns.NetworkScreen, "apply_class_lang_rules", _stub_rules)
    # Neither the initial refresh nor the 1 Hz status poll should run inside a
    # construction test: they are async and there is no event loop here. The
    # constructor only SCHEDULES them, so stubbing Clock keeps that scheduling
    # observable without executing it.
    monkeypatch.setattr(ns.Clock, "schedule_once", MagicMock(name="schedule_once"))
    monkeypatch.setattr(ns.Clock, "schedule_interval",
                        MagicMock(name="schedule_interval"))
    return fake


# ── (a) the radio QUERY is refused ─────────────────────────────────────

def test_construction_survives_a_refused_radio_query(nmcli):
    nmcli.radio.side_effect = Refused("Not authorized to perform this operation")

    screen = ns.NetworkScreen()   # must not raise: this is the whole point

    assert screen.nmcli_usable is False
    assert "Not authorized" in screen.status_text, (
        "the refusal must reach the screen's own Status box, the surface "
        "connect() failures already use"
    )


def test_a_refused_radio_query_is_logged_as_a_warning(nmcli, monkeypatch):
    nmcli.radio.side_effect = Refused("Not authorized to perform this operation")
    warnings = []
    # The module-level `log` is the same Logger child the missing-nmcli path
    # warns through, and the in-app Log Viewer reads. Captured at the logger
    # rather than through caplog: Kivy's Logger is not wired to the root
    # logger handlers pytest attaches.
    monkeypatch.setattr(ns.log, "warning",
                        lambda msg, *a, **kw: warnings.append(msg))
    ns.NetworkScreen()

    assert len(warnings) == 1
    assert "Not authorized to perform this operation" in warnings[0]
    assert "unavailable" in warnings[0]


def test_a_refused_radio_query_leaves_the_screen_inert(nmcli):
    nmcli.radio.side_effect = Refused("Not authorized")
    screen = ns.NetworkScreen()

    # No poller was started, so on_dismiss has nothing to cancel and
    # status_update never asks the refusing nmcli once a second.
    assert screen.status_update_task is None
    screen.on_dismiss()          # must not raise on the None
    assert ns.Clock.schedule_interval.call_count == 0
    assert screen.lock is True   # the controls stay disabled, as when nmcli is absent


def test_toggling_after_a_refused_query_issues_no_command(nmcli):
    nmcli.radio.side_effect = Refused("Not authorized")
    screen = ns.NetworkScreen()

    screen.wifi_enabled = True

    assert nmcli.radio.wifi_on.call_count == 0
    assert nmcli.radio.wifi_off.call_count == 0


# ── (b) the query works, but the radio COMMAND would be refused ────────

def test_construction_never_commands_the_radio(nmcli):
    """THE ORIGINAL DEFECT. radio() succeeds and reports wifi ON, so mirroring
    it is a False -> True property change -- the exact dispatch that used to
    call wifi_on(). wifi_on is armed to raise so that a regression fails loudly
    rather than merely being slow."""
    nmcli.radio.wifi_on.side_effect = Refused("Not authorized to perform this operation")
    nmcli.radio.wifi_off.side_effect = Refused("Not authorized to perform this operation")

    screen = ns.NetworkScreen()

    assert nmcli.radio.wifi_on.call_count == 0, (
        "construction commanded the radio instead of reflecting its state"
    )
    assert nmcli.radio.wifi_off.call_count == 0
    assert screen.wifi_enabled is True, "the reported radio state was not mirrored"
    assert screen.nmcli_usable is True, "a readable radio must not degrade the screen"
    assert screen.status_text == "", "no failure happened, so nothing to report"


def test_construction_mirrors_a_radio_that_is_off(nmcli):
    """The wifi-off case takes the no-change path through the property, which
    dispatches nothing at all. Pinned so that a future 'always dispatch to
    sync' change has to face both directions."""
    nmcli.radio.return_value = SimpleNamespace(wifi=False)
    nmcli.radio.wifi_off.side_effect = Refused("Not authorized")

    screen = ns.NetworkScreen()

    assert screen.wifi_enabled is False
    assert nmcli.radio.wifi_off.call_count == 0


def test_a_healthy_screen_starts_its_pollers(nmcli):
    """The guard flag must not have made the normal path inert."""
    ns.NetworkScreen()
    assert ns.Clock.schedule_once.call_count == 1
    assert ns.Clock.schedule_interval.call_count == 1


# ── (c) toggling after construction, when the command is refused ───────

def test_a_refused_toggle_is_logged_and_does_not_raise(nmcli):
    nmcli.radio.return_value = SimpleNamespace(wifi=False)
    nmcli.radio.wifi_on.side_effect = Refused("Not authorized to perform this operation")
    screen = ns.NetworkScreen()
    assert screen.nmcli_usable is True

    screen.wifi_enabled = True   # the operator presses the toggle

    assert nmcli.radio.wifi_on.call_count == 1, "the toggle must still try"
    assert screen.nmcli_usable is False
    assert "Not authorized to perform this operation" in screen.status_text


def test_a_refused_toggle_does_not_wedge_the_second_press(nmcli):
    nmcli.radio.return_value = SimpleNamespace(wifi=False)
    nmcli.radio.wifi_on.side_effect = Refused("Not authorized")
    screen = ns.NetworkScreen()

    screen.wifi_enabled = True
    screen.wifi_enabled = False   # must not raise, and must not command

    assert nmcli.radio.wifi_off.call_count == 0


# ── (d) the pre-existing missing-nmcli path is unchanged ───────────────

@pytest.fixture
def nmcli_absent(monkeypatch):
    fake = MagicMock(name="nmcli")
    monkeypatch.setattr(ns, "nmcli", fake)
    monkeypatch.setattr(ns, "NMCLI_AVAILABLE", False)
    monkeypatch.setattr(ns.NetworkScreen, "apply_class_lang_rules", _stub_rules)
    monkeypatch.setattr(ns.Clock, "schedule_once", MagicMock())
    monkeypatch.setattr(ns.Clock, "schedule_interval", MagicMock())
    return fake


def test_missing_nmcli_still_constructs_without_touching_nmcli(nmcli_absent):
    screen = ns.NetworkScreen()

    assert screen.status_update_task is None
    assert screen.lock is True
    assert nmcli_absent.radio.call_count == 0
    assert ns.Clock.schedule_interval.call_count == 0
    screen.on_dismiss()


def test_missing_nmcli_still_swallows_a_toggle(nmcli_absent):
    screen = ns.NetworkScreen()

    screen.wifi_enabled = True

    assert nmcli_absent.radio.wifi_on.call_count == 0
    # NMCLI_AVAILABLE is checked first, so the instance flag alone cannot
    # re-enable the calls on a machine with no nmcli at all.
    screen.nmcli_usable = True
    screen.wifi_enabled = False
    assert nmcli_absent.radio.wifi_off.call_count == 0


def test_the_async_paths_refuse_to_run_when_nmcli_is_unusable(nmcli):
    """``refresh``/``status_update``/``connect`` are guarded by the same flag.

    Driven without an event loop: the guard returns before the first ``await``,
    so the coroutine finishes on its first ``send()``. That is exactly the
    property being asserted -- a coroutine that got as far as awaiting nmcli
    would raise StopIteration only after issuing the call.
    """
    screen = ns.NetworkScreen()
    screen.nmcli_usable = False
    screen.device = "wlan0"
    nmcli.device.show.side_effect = AssertionError("nmcli called while unusable")
    nmcli.device.side_effect = AssertionError("nmcli called while unusable")
    nmcli.connection.side_effect = AssertionError("nmcli called while unusable")

    for coro in (screen.refresh(), screen.status_update(), screen.connect()):
        with pytest.raises(StopIteration):
            coro.send(None)
