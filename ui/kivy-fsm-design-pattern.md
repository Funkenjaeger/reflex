# Kivy + `transitions` FSM Architecture Guide

A design pattern for Kivy GUI applications that monitor and control machinery
using the `transitions` state machine library. Optimized for separation of
concerns, testability, and avoiding tight coupling between UI elements and
state machines.

## Table of Contents

1. [Core Principles](#core-principles)
2. [Layered Architecture](#layered-architecture)
3. [Event Bus](#event-bus)
4. [Hardware Abstraction Layer (HAL)](#hardware-abstraction-layer-hal)
5. [Domain FSM](#domain-fsm)
6. [Controller](#controller)
7. [UI FSM](#ui-fsm)
8. [Widgets and kv](#widgets-and-kv)
9. [Communication Rules](#communication-rules)
10. [Mode Inputs and Conditional Transitions](#mode-inputs-and-conditional-transitions)
11. [Multi-Step Configuration Flows](#multi-step-configuration-flows)
12. [Construction Order and Lifecycle](#construction-order-and-lifecycle)
13. [Testing Strategy](#testing-strategy)
14. [Anti-Patterns to Avoid](#anti-patterns-to-avoid)

---

## Core Principles

1. **One-way dependency: UI → FSM → HAL.** The UI knows about the controller;
   the controller knows about the FSMs and HAL; the FSMs know about the HAL.
   Nothing flows the other way as direct references.
2. **State changes broadcast as events; widgets observe.** FSM callbacks
   publish events; they never touch widgets directly.
3. **Declarative state-to-UI mapping.** A single policy table maps states to
   widget enablement/visibility/labels rather than scattering `if state == ...`
   throughout the code.
4. **Domain logic outside both UI and FSM.** Hardware I/O lives in a HAL; the
   FSM coordinates *what happens when*, not *how*.
5. **Two FSMs, never coupled directly.** A domain FSM models the machine; a UI
   FSM models operator interaction. They communicate only through the event
   bus (for notifications) and the controller (for commands).

## Layered Architecture

```
[Widgets / kv]
      ↓ user intents (button presses)
[Controller]            ← Kivy properties, UI policy, event subscriptions
      ↓ trigger calls (fsm.start(), fsm.stop())
[Domain FSM] [UI FSM]   ← state coordination, sequencing, guards
      ↓ method calls (hal.set_enable(True), hal.write_stop_position(x))
[HAL / hardware service]← register addresses, protocol details, retries
      ↓ bytes on the wire
[Firmware]
```

Each layer talks only to the one immediately below it. The controller is the
single chokepoint where user intent translates into FSM commands.

## Event Bus

A small dependency-free pub/sub mechanism that decouples publishers from
subscribers. A single app-wide instance is fine; inject it for stricter
testing.

```python
# event_bus.py
from collections import defaultdict
from typing import Callable, Any

class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable[..., Any]) -> Callable[[], None]:
        self._subs[event].append(handler)
        return lambda: self._subs[event].remove(handler)

    def publish(self, event: str, **payload) -> None:
        for h in list(self._subs[event]):
            h(**payload)

bus = EventBus()
```

**Event naming.** Prefix events to make their origin obvious:
`state_changed` for the domain FSM, `ui_state_changed` for the UI FSM,
`alarm_raised` and `position_updated` for hardware-level events.

## Hardware Abstraction Layer (HAL)

The HAL is the **single place** that knows about firmware register addresses
and protocols. Methods are named in **domain terms**, not register terms.

```python
# hal.py
class MachineHAL:
    """Domain-named operations. Implementations talk to firmware."""
    def set_enable(self, enabled: bool) -> None: ...
    def set_stop_position(self, mm: float) -> None: ...
    def start_spindle(self, rpm: int) -> None: ...
    def stop_spindle(self) -> None: ...
    def home_axes(self) -> None: ...
    def read_position(self) -> float: ...
    def read_alarm_code(self) -> int | None: ...

class ModbusMachineHAL(MachineHAL):
    REG_ENABLE      = 0x0040
    REG_STOP_POS    = 0x0044
    REG_SPINDLE_RPM = 0x0050

    def __init__(self, client):
        self.client = client

    def set_enable(self, enabled):
        self.client.write_register(self.REG_ENABLE, 1 if enabled else 0)

    def set_stop_position(self, mm):
        raw = int(mm * 1000)
        self.client.write_register(self.REG_STOP_POS, raw)

class MockMachineHAL(MachineHAL):
    """In-memory implementation for tests and headless development."""
    def __init__(self):
        self.enabled = False
        self.stop_position = 0.0
    def set_enable(self, enabled): self.enabled = enabled
    def set_stop_position(self, mm): self.stop_position = mm
```

**Why a HAL.** Firmware register changes become one-line edits. Protocol
changes (Modbus → EtherCAT) mean writing one new HAL implementation, with
nothing above the HAL changing. Unit tests inject a `MockMachineHAL` and
assert against simple Python attributes rather than mocking byte-level calls.

## Domain FSM

Models the physical machine. Knows nothing about Kivy, screens, or UI.
Uses `transitions` with an external model (recommended) so the model is a
plain Python class.

```python
# fsm.py
from transitions.extensions import HierarchicalMachine
from event_bus import bus

STATES = ["idle", "homing", "ready", "running", "retracting", "alarm"]

TRANSITIONS = [
    {"trigger": "home",      "source": "idle",       "dest": "homing"},
    {"trigger": "homed",     "source": "homing",     "dest": "ready"},
    {"trigger": "start",     "source": "ready",      "dest": "running"},
    # Conditional: order matters; first matching transition wins.
    {"trigger": "stop", "source": "running", "dest": "retracting",
     "conditions": "retract_enabled"},
    {"trigger": "stop", "source": "running", "dest": "ready"},
    {"trigger": "retracted", "source": "retracting", "dest": "ready"},
    {"trigger": "fault",     "source": "*",          "dest": "alarm"},
    {"trigger": "reset",     "source": "alarm",      "dest": "idle"},
]

class ToolDomain:
    """Plain Python class. The Machine is configured to use it as model."""
    def __init__(self, hal, modes):
        self.hal = hal
        self.modes = modes  # provides retract_enabled, speed, etc.

    # Conditions read flags via the injected `modes` object.
    def retract_enabled(self) -> bool:
        return bool(self.modes.retract_enabled)

    # State entry/exit callbacks call HAL and may auto-advance.
    def on_enter_homing(self):
        try:
            self.hal.home_axes()
            self.homed()
        except Exception as e:
            bus.publish("alarm_raised", reason=str(e))
            self.fault()

    def on_enter_running(self):
        self.hal.set_stop_position(self.modes.stop_position)
        self.hal.set_enable(True)
        self.hal.start_spindle(self.modes.speed)

    def on_exit_running(self):
        self.hal.stop_spindle()
        self.hal.set_enable(False)

    def on_enter_retracting(self):
        try:
            self.hal.move_to(self.modes.retract_position)
            self.retracted()
        except Exception as e:
            bus.publish("alarm_raised", reason=str(e))
            self.fault()

    # Broadcasting is the only thing that touches the bus on every transition.
    def _broadcast(self):
        bus.publish("state_changed", state=self.state)

def build_domain_fsm(hal, modes):
    domain = ToolDomain(hal, modes)
    HierarchicalMachine(
        model=domain, states=STATES, transitions=TRANSITIONS,
        initial="idle", after_state_change="_broadcast",
    )
    return domain
```

**Model style: external model.** Prefer `Machine(model=domain, ...)` over
`model=self` (FSM is the model) or inheritance. Reasons:
- Domain object is testable as plain Python without `transitions` ceremony.
- Cleanest separation between domain logic and FSM plumbing.
- Easy to mock; easy to swap FSM library if needed.
- Migration from `model=self` to external model is mechanical if you start
  with the simpler form.

**Where firmware writes go.** *Always* in the FSM's `on_enter_*` / `on_exit_*`
callbacks via the HAL — never in the controller. Register writes are
consequences of state changes; if the controller writes registers directly,
the software model and the physical machine will eventually disagree.

## Controller

The Kivy-facing layer. Owns both FSMs, exposes Kivy properties for kv to
bind to, translates user intents into trigger calls, marshals bus events
onto the main thread.

```python
# controller.py
from kivy.event import EventDispatcher
from kivy.properties import (
    BooleanProperty, StringProperty, NumericProperty, ObjectProperty
)
from kivy.clock import Clock

from event_bus import bus
from fsm import build_domain_fsm
from ui_fsm import build_ui_fsm
from hal import ModbusMachineHAL  # or MockMachineHAL for testing
from ui_policy import UI_POLICY, ACTIVE_CONFIG_BUTTON

class ToolController(EventDispatcher):
    # State-derived UI properties
    can_home  = BooleanProperty(False)
    can_start = BooleanProperty(False)
    can_stop  = BooleanProperty(False)
    status    = StringProperty("")
    alarm_msg = StringProperty("")

    # Mode flags (UI-controlled, read by FSM conditions)
    retract_enabled = BooleanProperty(False)

    # Configuration values (UI-controlled, read by FSM at trigger time)
    speed   = NumericProperty(0)
    feed    = NumericProperty(0)
    depth   = NumericProperty(0)
    coolant = StringProperty("")

    # Validation flags
    speed_ok   = BooleanProperty(False)
    feed_ok    = BooleanProperty(False)
    depth_ok   = BooleanProperty(False)
    coolant_ok = BooleanProperty(False)

    # Validation error messages
    speed_error   = StringProperty("")
    feed_error    = StringProperty("")
    depth_error   = StringProperty("")
    coolant_error = StringProperty("")

    # UI state
    ui_state         = StringProperty("main")
    active_config    = StringProperty("")
    current_step_valid = BooleanProperty(False)

    def __init__(self, hal=None, **kw):
        super().__init__(**kw)

        # 1. Property bindings for live validation
        self.bind(speed=lambda *_: self._validate_speed(),
                  feed=lambda *_: self._validate_feed(),
                  depth=lambda *_: self._validate_depth(),
                  coolant=lambda *_: self._validate_coolant())

        # 2. Build domain FSM with HAL and self-as-modes
        self.hal = hal or ModbusMachineHAL(...)
        self.fsm = build_domain_fsm(self.hal, modes=self)

        # 3. Build UI FSM last (it may reference fully-constructed controller)
        self.ui = build_ui_fsm(self)

        # 4. Subscribe to events after both FSMs exist
        bus.subscribe("state_changed",     self._on_domain_state)
        bus.subscribe("ui_state_changed",  self._on_ui_state)
        bus.subscribe("alarm_raised",      self._on_alarm)

        # 5. Sync initial UI to current state
        self._apply_policy(self.fsm.state)

        # 6. Bindings for derived UI helpers
        self.bind(ui_state=self._recompute_current_valid,
                  speed_ok=self._recompute_current_valid,
                  feed_ok=self._recompute_current_valid,
                  depth_ok=self._recompute_current_valid,
                  coolant_ok=self._recompute_current_valid)

    # --- Intents from the UI ---
    def press_home(self):  self.fsm.home()
    def press_start(self): self.fsm.start()
    def press_stop(self):
        # Multi-step UI interactions go through the UI FSM first.
        if self.fsm.state == "running":
            self.ui.ask_stop()
    def confirm_stop(self):
        self.ui.confirm()
        self.fsm.stop()
    def cancel_stop(self):
        self.ui.cancel()
    def press_reset(self): self.fsm.reset()

    # --- Event handlers (may arrive off the main thread) ---
    def _on_domain_state(self, state):
        Clock.schedule_once(lambda _dt: self._apply_policy(state), 0)

    def _on_ui_state(self, state):
        def apply(_dt):
            self.ui_state = state
            self.active_config = ACTIVE_CONFIG_BUTTON.get(state, "")
        Clock.schedule_once(apply, 0)

    def _on_alarm(self, reason):
        Clock.schedule_once(lambda _dt: setattr(self, "alarm_msg", reason), 0)

    # --- Policy application ---
    def _apply_policy(self, state):
        p = UI_POLICY[state]
        self.can_home  = p["can_home"]
        self.can_start = p["can_start"]
        self.can_stop  = p["can_stop"]
        self.status    = p["status"]

    # --- Validation (pure Python; reused by FSM and live UI feedback) ---
    @staticmethod
    def _check_speed(v):
        if v <= 0:    return False, "Must be > 0"
        if v > 12000: return False, "Exceeds spindle max (12000)"
        return True, ""

    def _validate_speed(self):
        ok, msg = self._check_speed(self.speed)
        self.speed_ok, self.speed_error = ok, msg

    # ... analogous _validate_feed/_depth/_coolant ...

    def _recompute_current_valid(self, *_):
        m = {"config_speed":   self.speed_ok,
             "config_feed":    self.feed_ok,
             "config_depth":   self.depth_ok,
             "config_coolant": self.coolant_ok}
        self.current_step_valid = m.get(self.ui_state, True)
```

**Threading note.** `transitions` callbacks run on whatever thread fires the
trigger. If hardware polling runs on a background thread, all UI-affecting
work (assigning to Kivy properties) must be marshaled onto the main thread
via `Clock.schedule_once`.

## UI FSM

Models operator interaction (wizards, modal dialogs, mode toggles). Lives in
its own module. **Holds a reference only to the controller**, never to the
domain FSM.

```python
# ui_fsm.py
from transitions import Machine
from event_bus import bus

UI_STATES = [
    "main",
    "config_speed", "config_feed", "config_depth", "config_coolant",
    "ready_to_run",
    "alarm_modal",
    "confirm_stop",
]

UI_TRANSITIONS = [
    {"trigger": "begin_config", "source": "main",           "dest": "config_speed"},
    {"trigger": "next", "source": "config_speed",   "dest": "config_feed",
     "conditions": "speed_valid"},
    {"trigger": "next", "source": "config_feed",    "dest": "config_depth",
     "conditions": "feed_valid"},
    {"trigger": "next", "source": "config_depth",   "dest": "config_coolant",
     "conditions": "depth_valid"},
    {"trigger": "next", "source": "config_coolant", "dest": "ready_to_run",
     "conditions": "coolant_valid"},
    {"trigger": "back", "source": "config_feed",    "dest": "config_speed"},
    {"trigger": "back", "source": "config_depth",   "dest": "config_feed"},
    {"trigger": "back", "source": "config_coolant", "dest": "config_depth"},
    {"trigger": "cancel", "source": ["config_speed", "config_feed",
                                     "config_depth", "config_coolant"],
                          "dest": "main"},
    {"trigger": "show_alarm", "source": "*",            "dest": "alarm_modal"},
    {"trigger": "ack_alarm",  "source": "alarm_modal", "dest": "main"},
    {"trigger": "ask_stop",   "source": "main",        "dest": "confirm_stop"},
    {"trigger": "cancel",     "source": "confirm_stop","dest": "main"},
    {"trigger": "confirm",    "source": "confirm_stop","dest": "main"},
]

class UIDomain:
    def __init__(self, controller):
        # Pure assignment only — do NOT call methods on the controller here.
        self.controller = controller

    # Validation conditions delegate to the controller.
    def speed_valid(self):   return self.controller.speed_ok
    def feed_valid(self):    return self.controller.feed_ok
    def depth_valid(self):   return self.controller.depth_ok
    def coolant_valid(self): return self.controller.coolant_ok

    def _broadcast(self):
        bus.publish("ui_state_changed", state=self.state)

def build_ui_fsm(controller):
    domain = UIDomain(controller)
    Machine(
        model=domain, states=UI_STATES, transitions=UI_TRANSITIONS,
        initial="main", after_state_change="_broadcast",
    )
    return domain
```

**When to add a UI FSM.** Reach for one when:
- Three or more interaction states with non-trivial transitions.
- The same interaction state affects multiple widgets.
- You need guards or "can't get there from here" logic.
- You want to unit-test the flow.

A simple two-state toggle (panel open/closed) is just a `BooleanProperty`.

## Widgets and kv

### Custom widgets own presentation behavior

Animation, color, and per-widget lifecycle belong in the widget. The widget
exposes a single `BooleanProperty` for *whether* to animate; the controller
decides *whether* via a binding.

```python
# widgets.py
from kivy.uix.button import Button
from kivy.properties import BooleanProperty, ListProperty
from kivy.animation import Animation

class BlinkingButton(Button):
    blinking      = BooleanProperty(False)
    blink_color   = ListProperty([1, 0.8, 0.2, 1])
    normal_color  = ListProperty([0.3, 0.3, 0.3, 1])

    def __init__(self, **kw):
        super().__init__(**kw)
        self._anim = None
        self.bind(blinking=self._on_blinking)

    def _on_blinking(self, _inst, value):
        if self._anim:
            self._anim.cancel(self)
            self._anim = None
        if value:
            self.background_color = self.blink_color
            anim = (Animation(background_color=self.normal_color, duration=0.5)
                    + Animation(background_color=self.blink_color,  duration=0.5))
            anim.repeat = True
            self._anim = anim
            anim.start(self)
        else:
            self.background_color = self.normal_color
```

**Never put animation logic in the controller.** That couples the controller
to Kivy's `Animation` class and breaks headless testing.

### kv binds declaratively to controller properties

No `if state == ...` in widget code or kv. Bind directly to controller
properties.

```kv
<ToolScreen>:
    BoxLayout:
        orientation: "vertical"
        Label:
            text: "Status: " + root.controller.status
        Label:
            text: root.controller.alarm_msg
            color: 1, 0.3, 0.3, 1
        BoxLayout:
            Button:
                text: "Home"
                disabled: not root.controller.can_home
                on_release: root.controller.press_home()
            Button:
                text: "Start"
                disabled: not root.controller.can_start
                on_release: root.controller.press_start()
            Button:
                text: "Stop"
                disabled: not root.controller.can_stop
                on_release: root.controller.press_stop()

<ConfigScreen>:
    BoxLayout:
        orientation: "vertical"
        BlinkingButton:
            text: "Speed: %s" % root.controller.speed
            blinking: root.controller.active_config == "speed"
            on_release: app.open_speed_entry()
        Label:
            text: root.controller.speed_error
            color: 1, 0.4, 0.4, 1
        # ...feed, depth, coolant analogous...
        BoxLayout:
            Button:
                text: "Back"
                on_release: root.controller.ui.back()
            Button:
                text: "Next"
                disabled: not root.controller.current_step_valid
                on_release: root.controller.ui.next()
            Button:
                text: "Cancel"
                on_release: root.controller.ui.cancel()
```

### Policy tables centralize state-to-UI mapping

```python
# ui_policy.py
UI_POLICY = {
    "idle":       {"can_home": True,  "can_start": False, "can_stop": False, "status": "Idle"},
    "homing":     {"can_home": False, "can_start": False, "can_stop": False, "status": "Homing…"},
    "ready":      {"can_home": True,  "can_start": True,  "can_stop": False, "status": "Ready"},
    "running":    {"can_home": False, "can_start": False, "can_stop": True,  "status": "Running"},
    "retracting": {"can_home": False, "can_start": False, "can_stop": False, "status": "Retracting…"},
    "alarm":      {"can_home": False, "can_start": False, "can_stop": False, "status": "ALARM"},
}

ACTIVE_CONFIG_BUTTON = {
    "config_speed":   "speed",
    "config_feed":    "feed",
    "config_depth":   "depth",
    "config_coolant": "coolant",
}
```

Adding a new state is: add it to `STATES`/`TRANSITIONS`, add a row to the
relevant policy table. No widget code changes.

## Communication Rules

The two FSMs share the event bus but never reference each other directly.
Communication has a **directional flavor**:

| Publisher                      | Allowed subscribers                  |
|--------------------------------|--------------------------------------|
| Domain FSM                     | UI FSM, controller, anything else    |
| Hardware monitor / HAL events  | Domain FSM, controller               |
| UI FSM                         | Controller (and other UI components) |
| Controller                     | UI components, other controllers     |

**Domain → UI: via the bus.** UI FSM subscribes to `state_changed` and
`alarm_raised` and reacts. Healthy decoupling.

**UI → Domain: via the controller.** UI FSM does not call domain triggers
directly and does not publish "please do X" commands on the bus. It calls a
controller method, which validates and calls the domain trigger. The
controller is the chokepoint where authorization, logging, and safety
interlocks live.

**Domain never subscribes to UI events.** Even if convenient, this couples
the domain to UI vocabulary and breaks "domain runs without Kivy."

**Mental model: bus carries past tense; method calls carry imperative.**
"Alarm raised" goes on the bus. "Stop the spindle" doesn't.

## Mode Inputs and Conditional Transitions

UI-controlled flags that affect FSM behavior follow this pattern:

1. Controller exposes the flag as a `BooleanProperty` (or other Kivy
   property), bound two-way to a kv widget.
2. The FSM is constructed with the controller as its `modes` argument
   (dependency injection).
3. The FSM defines a method (`retract_enabled(self)`) that reads
   `self.modes.retract_enabled`.
4. The relevant transition uses `"conditions": "retract_enabled"`.
5. `transitions` evaluates the condition at trigger time, not toggle time —
   the operator's latest setting wins.

```python
TRANSITIONS = [
    {"trigger": "stop", "source": "running", "dest": "retracting",
     "conditions": "retract_enabled"},
    {"trigger": "stop", "source": "running", "dest": "ready"},
]
```

**Multiple conditions.** `"conditions"` accepts a list; `"unless"` provides
negation: `{"conditions": ["retract_enabled"], "unless": ["door_open"]}`.

**Runtime arguments.** Pass via the trigger call: `self.stop(rpm=current_rpm)`.
`transitions` forwards kwargs to conditions and callbacks.

## Multi-Step Configuration Flows

For wizards/configuration sequences:

| Concern                                    | Lives in                           |
|--------------------------------------------|------------------------------------|
| How to blink (animation, colors, timing)   | Custom widget                      |
| Which button blinks right now              | Controller `active_config` + kv    |
| What "valid" means for each value          | Controller `_check_*` methods      |
| Whether the value is currently valid       | Controller `*_ok` properties       |
| Whether `next` is allowed                  | UI FSM transition conditions       |
| When to re-validate                        | Controller bindings on value change|
| Showing error messages                     | `*_error` StringProperty + kv Label|

**Defense in depth for advancement.** The "Next" button is disabled when
invalid (immediate UI feedback) *and* the FSM has the same condition as a
guard. If another path triggers `next` (keyboard shortcut, remote command),
the guard still protects you.

**Cross-field validation.** If `feed` validity depends on `speed`, have
`_validate_feed` read `self.speed` and add `speed=...` to the bindings that
call `_validate_feed`. Kivy reactivity makes this cheap.

**Don't put validation in the widget.** A `TextInput`'s `input_filter="float"`
for cheap formatting hints is fine; the *decision* about validity stays in
the controller as a single source of truth observable by both kv and FSM.

## Construction Order and Lifecycle

Sharing `self` from a partially-constructed controller to a child object is
safe in Python *as long as* the child only stores the reference and doesn't
*use* attributes until construction completes.

**Required order in `Controller.__init__`:**

1. Initialize Kivy properties (Kivy handles defaults; be explicit if needed).
2. Wire validation/derived-state bindings.
3. Build the domain FSM (it may reference `self` as `modes`).
4. Build the UI FSM **last** (it references the fully-constructed
   controller).
5. Subscribe to bus events.
6. Apply initial state policy.

**Rules:**

- `UIDomain.__init__` does pure assignment only. No method calls on
  `self.controller`, no bus publishes, no FSM triggers.
- Don't enable `transitions`' "fire callbacks on initial state" option
  unless you've confirmed the controller is fully built when it fires.
- If `Machine`'s `after_state_change` fires during `Machine.__init__`
  (configuration-dependent), make sure subscribers are registered *after*
  the FSM is built, not before.

**Optional belt-and-suspenders.** A two-phase init makes the safety
structural rather than conventional:

```python
class UIDomain:
    def __init__(self, controller):
        self.controller = controller   # store only
    def start(self):
        # Safe to use self.controller here
        bus.publish("ui_state_changed", state=self.state)
```

Controller calls `self.ui.start()` as the last line of `__init__`.

## Testing Strategy

Each layer is independently testable:

**HAL.** Trivial; substitute `MockMachineHAL` or assert against
`ModbusMachineHAL` with a mock client.

**Domain FSM.** Inject `MockMachineHAL` and a `SimpleNamespace` for modes:

```python
from types import SimpleNamespace
hal = MockMachineHAL()
modes = SimpleNamespace(retract_enabled=True, speed=1000, ...)
fsm = build_domain_fsm(hal, modes)
fsm.home(); fsm.homed(); fsm.start(); fsm.stop()
assert fsm.state == "retracting"
assert hal.enabled is False  # exit_running disabled it
```

**UI FSM.** Same shape — inject a `SimpleNamespace` controller stub:

```python
controller_stub = SimpleNamespace(speed_ok=True, feed_ok=False, ...)
ui = build_ui_fsm(controller_stub)
ui.begin_config()
ui.next()
assert ui.state == "config_feed"
ui.next()  # blocked by feed_valid
assert ui.state == "config_feed"
```

**Controller.** Test by publishing fake bus events and asserting on Kivy
properties; runs without a Kivy `App`.

**Validation logic.** Pure functions (`_check_speed` etc.) are unit-testable
without any infrastructure.

## Anti-Patterns to Avoid

- **Domain FSM importing or referencing the UI FSM, controller, or any
  Kivy types.** Breaks headless testing and re-tangles layers.
- **UI FSM calling `controller.fsm.start()` directly.** Bypasses the
  controller's chokepoint. Always go through a controller method.
- **Controller writing firmware registers directly.** Software model and
  physical machine will desynchronize. All register writes flow through the
  domain FSM via the HAL.
- **Domain FSM subscribing to UI events.** Implicit dependency on UI
  vocabulary; breaks "domain runs without Kivy."
- **Commands on the bus.** Events are notifications of fact ("alarm raised").
  Commands ("stop the spindle") need a single identifiable caller and
  return/raise semantics; they go via method calls.
- **`if state == ...` scattered through widget or controller code.**
  Centralize in a policy table keyed by state.
- **Animation logic in the controller.** Couples controller to Kivy
  internals; widgets own their animation.
- **Validation in widgets.** Validity needs to be observable from the FSM;
  put it in the controller as a single source of truth.
- **`UIDomain.__init__` calling methods on `self.controller`.** May run
  before the controller finishes constructing.
- **Subscribing to bus events before both FSMs exist.** A subscriber may
  fire on a half-built controller.
- **Inheriting the FSM from `Machine`/`HierarchicalMachine`.** Couples
  domain types to the FSM library and complicates MRO. Use external model
  (`Machine(model=domain, ...)`) instead.
- **Multiple modules writing to the same firmware register.** Lose the
  ability to add logging/retries/interlocks at one chokepoint.
- **Polling hardware on a background thread and updating Kivi properties
  without `Clock.schedule_once`.** Properties must be assigned on the main
  thread.
