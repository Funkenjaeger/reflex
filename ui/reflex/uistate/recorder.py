"""Records a UI state code on every relevant visual change.

MODELLED ON ``reflex/fsms/els_diag.py``, AND FOR THE SAME REASONS. Read that
module's docstring; the three properties it insists on apply here almost
verbatim:

1. **Cheap when nothing is happening.** Capture is driven by state-change
   events, never by ``board.update_tick``. Binding to the tick would log
   continuously as the DRO digits move, which is both useless (nothing changed
   but a number that was already going to be re-read) and ruinous on a Pi.

2. **Expensive work only on a real change.** Every trigger coalesces onto one
   ``Clock`` frame, so the burst of property writes from a single
   ``_apply_policy()`` pass produces ONE snapshot. That snapshot's field values
   are hashed first, cheaply; only if they differ from the last recorded frame
   does the widget walk, compression and file write happen.

3. **Incapable of taking the UI down.** A diagnostic that can break the machine
   control surface is worse than no diagnostic. Every stage is guarded, and
   repeated write failures DISABLE the recorder rather than retrying forever
   against a fault that is not going to clear (a full SD card, most likely).

THE LOG VIEWER STAYS READABLE. ``reflex/utils/log_levels.py`` exists because the
touchscreen log viewer IS the diagnostic instrument at the lathe, and 365 lines
of ``transitions`` chatter made it unusable. A 300-character code on every state
change would repeat exactly that mistake, so the Kivy log gets only a short
DEBUG line -- event name plus a code prefix, enough to line a moment in the log
up against a record in the JSONL. The full code lives in the JSONL alone.
"""

import json
import os
from datetime import datetime, timezone

from kivy.clock import Clock
from kivy.logger import Logger

from reflex.fsms.fsm_event_bus import fsm_event_bus as bus
from reflex.uistate import digest as digest_mod
from reflex.uistate import schema as schema_mod
# Importing the declaration module is what populates KNOWN_SCHEMAS. Without
# it the registry is empty and the recorder goes inert -- silently, which is
# exactly the failure mode `els_diag`'s KNOWN_SCHEMAS gate is known for.
import reflex.uistate.schema_v1  # noqa: F401
from reflex.utils.paths import uistate_dir

log = Logger.getChild(__name__)

# Events the FSMs already publish. Nothing new is published for this feature --
# these are exactly the moments worth reconstructing.
FSM_EVENTS = (
    "ui_state_changed",
    "state_changed",
    "els_alarm",
    "alarm_raised",
    "els_stop_activated",
    "els_retract_done",
    "els_pass_interrupted",
)

# Seconds to let the UI settle before observing ANYTHING. Both halves of a
# snapshot -- the field values and the widget tree -- are read at the same
# settled instant, which is what keeps them describing the same picture.
#
# It has to be a time delay, not a frame count, because the things being waited
# on are themselves timed: `HomePage.change_mode` defers through
# `Clock.schedule_once(..., 0.1)` and then re-schedules while the servo is still
# moving, and kv rules that resize a container lay out on a later frame. Reading
# too early recorded a take-up banner 30 px outside its own container, and ELS
# widgets still mounted after a switch to DRO -- neither of which any correctly
# settled replay can reproduce, so the drift guard reported them on every frame.
#
# The cost is latency in the log, not in the UI: a capture is a diagnostic
# record, and nothing waits on it.
SETTLE_SECONDS = 0.15

# How many settle windows to wait for a frame to stop changing before recording
# it anyway. A genuinely animated screen must still produce a record -- a
# storyboard with a hole in it is worse than one with a blurred page -- so this
# is a backstop, not a filter.
MAX_SETTLE_PROBES = 8

MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 5

# A hard ceiling, so no conceivable trigger storm can turn this into a
# disk-filling loop. Well above any real interaction rate.
MAX_RECORDS_PER_SECOND = 20

# After this many consecutive write failures the recorder gives up for good.
MAX_WRITE_FAILURES = 5


class UiStateRecorder:
    """Watches for visual state changes and appends a code per change."""

    def __init__(self, app, path=None):
        self.app = app
        self.enabled = os.environ.get("REFLEX_UISTATE", "on").lower() not in (
            "0", "off", "false", "no")
        self.verbose = os.environ.get("REFLEX_UISTATE_VERBOSE", "").lower() in (
            "1", "on", "true", "yes")
        self._path = path
        self._schema = None
        self._last_hash = None
        self._pending = None
        self._probe = None
        self._probes = 0
        self._write_failures = 0
        self._window = []
        self._unsubscribes = []
        self.records_written = 0

        if not self.enabled:
            log.info("uistate: disabled by REFLEX_UISTATE")
            return
        try:
            self._schema = schema_mod.current_schema()
        except RuntimeError as e:
            log.error(f"uistate: no schema registered ({e}); recorder inert")
            self.enabled = False

    # ── wiring ─────────────────────────────────────────────────────────────

    def start(self):
        """Subscribe to everything that can change the picture.

        Deliberately NOT ``board.update_tick`` -- see property 1 above.
        """
        if not self.enabled:
            return
        for event in FSM_EVENTS:
            self._unsubscribes.append(
                bus.subscribe(event, self._on_bus_event(event)))

        app = self.app
        self._bind(app.manager, "current", "screen")
        self._bind(app, "current_mode", "mode")
        self._bind(app.formats, "theme", "theme")
        self._bind(app.formats, "current_format", "units")
        self._bind(app.board, "connected", "connection")
        for name in ("ui_state", "instruction_text", "action_button_text",
                     "alarm_text", "active_input", "takeup_warning",
                     "reframe_message", "in_cycle", "engaged",
                     "els_stop_active", "reframe_confirm_pending",
                     "targets_reframed_warn", "depth_reached"):
            self._bind(app.els_uic, name, f"uic.{name}")
        try:
            els_bar = app.manager.get_screen("home").els_bar
            self._bind(els_bar, "enable_advanced", "els.advanced")
            self._bind(els_bar, "mode_name", "els.mode_name")
            self._bind(els_bar, "feed_name", "els.feed_name")
        except Exception as e:  # noqa: BLE001 - home may not be built yet
            log.debug(f"uistate: ELS bar not bindable yet ({e})")

        # Modal open/close, without touching the ~20 `.open()` call sites.
        try:
            from kivy.core.window import Window
            Window.bind(children=lambda *_: self.request("modal"))
        except Exception as e:  # noqa: BLE001 - headless contexts have no Window
            log.debug(f"uistate: no Window to watch for modals ({e})")

        log.info(f"uistate: recording schema {self._schema.id} to {self.path}")
        self.request("start")

    def _bind(self, target, name, label):
        if target is None:
            return
        try:
            target.bind(**{name: lambda *_a, _l=label: self.request(_l)})
        except Exception as e:  # noqa: BLE001 - a missing property must not abort wiring
            log.debug(f"uistate: cannot bind {label} ({e})")

    def _on_bus_event(self, event):
        def handler(**_payload):
            self.request(event)
        return handler

    def stop(self):
        for unsubscribe in self._unsubscribes:
            try:
                unsubscribe()
            except (ValueError, KeyError):
                pass
        self._unsubscribes.clear()

    # ── capture ────────────────────────────────────────────────────────────

    def request(self, reason: str):
        """Ask for a snapshot. Coalesces to one per settle window."""
        if not self.enabled:
            return
        if self._pending is None:
            self._pending = reason
            Clock.schedule_once(self._capture, SETTLE_SECONDS)

    def _capture(self, _dt):
        reason, self._pending = self._pending, None
        if not self.enabled:
            return
        try:
            self._capture_now(reason or "unknown")
        except Exception as e:  # noqa: BLE001 - capture must never reach the UI
            log.exception(f"uistate: capture failed ({e})")

    def _capture_now(self, reason: str):
        values = schema_mod.snapshot(self.app, self._schema)

        # Cheap change test FIRST: no widget walk, no compression, no write for
        # a trigger that did not actually alter the picture.
        fingerprint = hash(tuple(
            (key, values[key]) for key in self._schema.keys))
        if fingerprint == self._last_hash:
            self._probe = None
            self._probes = 0
            return

        # Then check the picture has STOPPED CHANGING, rather than guessing a
        # delay long enough to have covered it. Both halves are probed: property
        # values settle when the FSM does, but geometry keeps moving for a frame
        # or two afterwards (kv container resizes), and `HomePage.change_mode`
        # re-schedules itself for as long as the servo is moving. Recording
        # mid-transition produced captures no correctly settled replay could
        # reproduce -- a take-up banner 30 px outside its container, ELS widgets
        # still mounted after a switch to DRO -- which the drift guard then
        # reported on every frame.
        digests = digest_mod.subtree_digests(self.app)
        stable = hash(tuple(
            (key, values[key]) for key in self._schema.stable_keys))
        probe = (stable, digests.get("all"))

        if probe != self._probe:
            # Still moving. Remember where it got to and look again next window.
            self._probe = probe
            self._probes += 1
            if self._probes <= MAX_SETTLE_PROBES:
                self._pending = reason
                Clock.schedule_once(self._capture, SETTLE_SECONDS)
                return
            log.debug(f"uistate: {reason} never settled; recording as-is")

        self._probe = None
        self._probes = 0

        if not self._allow_rate():
            return

        code = schema_mod.encode(values, digests, self._schema)
        self._last_hash = fingerprint

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "schema": self._schema.id,
            "app": getattr(self.app, "version", ""),
            "ev": reason,
            "code": code,
        }
        if self._append(record):
            self.records_written += 1
            # Short, DEBUG, prefix only. The full code stays out of the log
            # viewer on purpose -- see the module docstring.
            log.debug(f"uistate: {reason} {code[:12]}...")
            if self.verbose:
                self._append_verbose(record["ts"])

    def _allow_rate(self) -> bool:
        now = Clock.get_boottime()
        self._window = [t for t in self._window if now - t < 1.0]
        if len(self._window) >= MAX_RECORDS_PER_SECOND:
            return False
        self._window.append(now)
        return True

    # ── storage ────────────────────────────────────────────────────────────

    @property
    def path(self):
        if self._path is None:
            self._path = uistate_dir() / "uistate.jsonl"
        return self._path

    def _rotate_if_needed(self):
        path = self.path
        if not path.exists() or path.stat().st_size < MAX_BYTES:
            return
        oldest = path.with_suffix(f".jsonl.{MAX_FILES}")
        if oldest.exists():
            oldest.unlink()
        for index in range(MAX_FILES - 1, 0, -1):
            older = path.with_suffix(f".jsonl.{index}")
            if older.exists():
                older.rename(path.with_suffix(f".jsonl.{index + 1}"))
        path.rename(path.with_suffix(".jsonl.1"))

    def _append(self, record) -> bool:
        try:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._write_failures = 0
            return True
        except OSError as e:
            self._write_failures += 1
            log.warning(f"uistate: write failed ({e}) "
                        f"[{self._write_failures}/{MAX_WRITE_FAILURES}]")
            if self._write_failures >= MAX_WRITE_FAILURES:
                # Disable rather than retry forever: on a full card this would
                # otherwise log a failure on every state change, for good.
                self.enabled = False
                self.stop()
                log.error("uistate: disabling recorder after repeated write "
                          "failures")
            return False

    def _append_verbose(self, stamp: str):
        try:
            path = self.path.with_suffix(".widgets.txt")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"# {stamp}\n")
                for line in digest_mod.describe_tree(self.app):
                    handle.write(line + "\n")
        except OSError as e:
            log.debug(f"uistate: verbose dump failed ({e})")
