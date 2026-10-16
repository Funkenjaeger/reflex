"""The Network screen's "Error reporting" switch reads as a POSITIVE switch.

The stored setting is ``Formats.disable_error_reporting`` and its meaning has
not changed: ``True`` means reporting is OFF (and it is the default). The
switch used to be labelled "Disable Error Reporting" and showed that value
as it stood, so ON meant "reporting is off". It is now labelled "Error
reporting", where ON means reporting is on, and the kv inverts what the
switch shows and what it writes. Nothing stored moves, so there is no
migration.

The binding lives in ``network_screen.kv``. Building that screen's real
widget tree under the mock GL backend is not possible (see
``test_network_screen_nmcli.py``), so these tests parse the kv with Kivy's
own parser, take the two expressions the switch is wired with, and run them
against a stand-in for ``app.formats``. That exercises the code that ships
rather than a copy of it.
"""
import os
from types import SimpleNamespace

import pytest
from kivy.lang.parser import Parser

import reflex
from reflex.dispatchers.formats import FormatsDispatcher

KV = os.path.join(os.path.dirname(reflex.__file__),
                  "components", "screens", "network_screen.kv")
LABEL = "Error reporting"
KEY = "disable_error_reporting"


def _switch():
    """The BooleanItem rule that carries the error-reporting switch."""
    with open(KV, encoding="utf-8") as fh:
        parser = Parser(content=fh.read(), filename=KV)
    found = []

    def walk(rule):
        for child in rule.children:
            name = child.properties.get("name")
            if child.name == "BooleanItem" and name is not None \
                    and name.co_value == LABEL:
                found.append(child)
            walk(child)

    for _, rule in parser.rules:
        walk(rule)
    assert len(found) == 1, f"expected one BooleanItem named {LABEL!r}"
    return found[0]


def _shown(stored_disabled):
    """What the switch displays when the stored flag has this value."""
    expr = _switch().properties["value"].value
    app = SimpleNamespace(formats=SimpleNamespace(**{KEY: stored_disabled}))
    return eval(expr, {"app": app})


def _written(switch_value, stored_before):
    """The stored flag after the operator sets the switch to this value."""
    handlers = [h for h in _switch().handlers if h.name == "on_value"]
    assert len(handlers) == 1
    app = SimpleNamespace(formats=SimpleNamespace(**{KEY: stored_before}))
    exec(handlers[0].value, {"app": app,
                             "self": SimpleNamespace(value=switch_value)})
    return getattr(app.formats, KEY)


def test_switch_is_labelled_positively():
    assert _switch().properties["name"].co_value == "Error reporting"


@pytest.mark.parametrize("stored_disabled, shown", [
    (True, False),    # reporting disabled in the file -> switch OFF
    (False, True),    # reporting enabled in the file  -> switch ON
])
def test_switch_shows_the_inverse_of_the_stored_flag(stored_disabled, shown):
    assert _shown(stored_disabled) is shown


@pytest.mark.parametrize("switch_value, stored_disabled", [
    (True, False),    # switch ON  -> stored "disabled = false"
    (False, True),    # switch OFF -> stored "disabled = true"
])
def test_switch_writes_the_inverse_to_the_stored_flag(switch_value,
                                                      stored_disabled):
    # Start from the opposite stored value so a no-op write cannot pass.
    assert _written(switch_value, not stored_disabled) is stored_disabled


def test_stored_setting_keeps_its_key_and_default():
    """No migration: the key is unchanged and the default is still "off"."""
    prop = FormatsDispatcher.disable_error_reporting
    assert prop.defaultvalue is True
