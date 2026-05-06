from kivy.logger import Logger
from transitions.extensions import HierarchicalMachine
from transitions.extensions.nesting import NestedState
from rcp.fsms.fsm_event_bus import fsm_event_bus as bus

NestedState.separator = '.'

log = Logger.getChild(__name__)

UI_STATES = [ "idle",
              "set_stop_z",
              "set_retract_z",
              "set_start_dia",
              "set_stop_dia",
              "confirm",
              {
                  "name": "in_cycle",
                  "children": [
                      "waiting_to_cut",
                      {"name": "cutting", "on_enter": "on_enter_in_cycle_cutting"},
                      "waiting_to_retract",
                      {"name": "retracting", "on_enter": "on_enter_in_cycle_retracting"},
                  ],
                  "initial": "waiting_to_cut"
              },
              "alarm",
]

UI_TRANSITIONS = [
    {"trigger": "start", "source": "idle", "dest": "set_stop_z"},
    {"trigger": "action", "source": "set_stop_z", "dest": "set_retract_z", "conditions": "stop_z_valid"},
    {"trigger": "action", "source": "set_retract_z", "dest": "set_start_dia", "conditions": "retract_z_valid"},
    {"trigger": "action", "source": "set_start_dia", "dest": "set_stop_dia", "conditions": "start_dia_valid"},
    {"trigger": "action", "source": "set_stop_dia", "dest": "confirm", "conditions": "stop_dia_valid"},
    {"trigger": "action", "source": "confirm", "dest": "in_cycle"},
    {"trigger": "action", "source": "in_cycle.waiting_to_cut", "dest": "in_cycle.cutting"},
    {"trigger": "cut_done", "source": "in_cycle.cutting", "dest": "in_cycle.waiting_to_retract"},
    {"trigger": "action", "source": "in_cycle.waiting_to_retract", "dest": "in_cycle.retracting"},
    {"trigger": "retract_done", "source": "in_cycle.retracting", "dest": "in_cycle.waiting_to_cut"},

    # ─── Reverse navigation: back one wizard step ────────────────────────────
    {"trigger": "back", "source": "set_retract_z", "dest": "set_stop_z"},
    {"trigger": "back", "source": "set_start_dia", "dest": "set_retract_z"},
    {"trigger": "back", "source": "set_stop_dia",  "dest": "set_start_dia"},
    {"trigger": "back", "source": "confirm",       "dest": "set_stop_dia"},

    # ─── Cancel: from anywhere in the configuration / cycle, return to idle ──
    {"trigger": "cancel", "source": ["set_stop_z", "set_retract_z",
                                     "set_start_dia", "set_stop_dia",
                                     "confirm", "in_cycle"],
                          "dest": "idle"},

    # ─── Alarm handling ──────────────────────────────────────────────────────
    {"trigger": "fault", "source": "*", "dest": "alarm"},
    {"trigger": "ack_alarm", "source": "alarm", "dest": "idle"},
]


class ElsUiFsm:
    def __init__(self, controller):
        self.controller = controller
        self.fsm = HierarchicalMachine(
            model = self, 
            states = UI_STATES, 
            transitions = UI_TRANSITIONS,
            initial="idle", 
            after_state_change="_broadcast",
            queued=True,
        )
        bus.subscribe("els_stop_activated", self.on_event_els_stop_activated)
        bus.subscribe("els_retract_done", self.on_event_els_retract_done)

    # ——— after any state change ———
    def _broadcast(self):
        bus.publish("ui_state_changed", state=self.state)

    # ——— condition-checking methods ———
    def stop_z_valid(self):     return self.controller.stop_z_valid
    def retract_z_valid(self):  return self.controller.retract_z_valid
    def start_dia_valid(self):  return self.controller.start_dia_valid
    def stop_dia_valid(self):   return self.controller.stop_dia_valid

    # ——— state change methods ———
    def on_enter_in_cycle_cutting(self):
        log.info("on_enter_in_cycle_cutting()")
        self.controller.start_cut()

    def on_enter_in_cycle_retracting(self):
        log.info("on_enter_in_cycle_retracting()")
        self.controller.start_retract()

    # ——— event handlers ———
    def on_event_els_stop_activated(self):
        log.info("ui fsm on_event_els_stop_activated()")
        if self.state == "in_cycle.cutting":
            self.cut_done()
    
    def on_event_els_retract_done(self):
        log.info("ui fsm on_event_els_retract_done()")
        if self.state == "in_cycle.retracting":
            self.retract_done()
