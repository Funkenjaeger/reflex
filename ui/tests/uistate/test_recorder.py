"""The recorder's three load-bearing properties.

Cheap when idle, expensive only on a real change, and incapable of taking the
UI down -- the same three ``ElsDiagRecorder`` is built around.
"""

import json

import pytest

from reflex.uistate import recorder as recorder_mod
from reflex.uistate.recorder import UiStateRecorder


class FakeClock:
    """Stands in for kivy.clock.Clock so a test can control coalescing."""

    def __init__(self):
        self.scheduled = []
        self.boottime = 0.0

    def schedule_once(self, callback, _timeout=0):
        self.scheduled.append(callback)

    def get_boottime(self):
        return self.boottime

    def run(self, max_generations=10):
        """Drain scheduled callbacks until quiet.

        A capture spans several frames on purpose: values are snapshotted at
        once, the widget tree is observed only after Kivy's layout has settled
        (``recorder.SETTLE_FRAMES``), so a single generation is not a frame's
        worth of work here.
        """
        for _ in range(max_generations):
            if not self.scheduled:
                return
            pending, self.scheduled = self.scheduled, []
            for callback in pending:
                callback(0)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(recorder_mod, "Clock", fake)
    return fake


@pytest.fixture
def rec(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(recorder_mod.digest_mod, "subtree_digests",
                        lambda app: {"all": 1})

    class App:
        version = "vtest"

    recorder = UiStateRecorder(App(), path=tmp_path / "uistate.jsonl")
    assert recorder.enabled, "schema_v1 must be registered by importing recorder"

    # A full default snapshot, so encode() sees every declared field; tests
    # mutate `recorder.state` to simulate the picture changing.
    recorder.state = {f.key: f.default for f in recorder._schema.fields}
    recorder.state["screen"] = "home"
    monkeypatch.setattr(recorder_mod.schema_mod, "snapshot",
                        lambda app, schema: dict(recorder.state))
    return recorder


def lines(rec):
    if not rec.path.exists():
        return []
    return [json.loads(line) for line in
            rec.path.read_text(encoding="utf-8").splitlines() if line]


def test_a_burst_of_triggers_coalesces_to_one_record(rec, clock):
    """One _apply_policy() pass writes many properties; that is ONE frame."""
    for _ in range(10):
        rec.request("uic.instruction_text")
    assert len(clock.scheduled) == 1
    clock.run()
    assert len(lines(rec)) == 1


def test_an_unchanged_state_writes_nothing(rec, clock):
    """The cheap fingerprint check is what keeps the widget walk off the Pi."""
    rec.request("first")
    clock.run()
    assert len(lines(rec)) == 1

    rec.request("same-again")
    clock.run()
    assert len(lines(rec)) == 1, "an identical snapshot must not be recorded"


def test_a_real_change_is_recorded(rec, clock):
    rec.request("first")
    clock.run()
    rec.state["screen"] = "setup_screen"
    rec.request("navigated")
    clock.run()

    records = lines(rec)
    assert len(records) == 2
    assert records[1]["ev"] == "navigated"


def test_the_widget_walk_never_runs_for_an_unchanged_frame(rec, clock, monkeypatch):
    """The cheap fingerprint check is what keeps the walk off an idle Pi."""
    calls = []
    monkeypatch.setattr(recorder_mod.digest_mod, "subtree_digests",
                        lambda app: calls.append(1) or {"all": 1})
    rec.request("first")
    clock.run()
    before = len(calls)
    assert before >= 1

    rec.request("unchanged")
    clock.run()
    assert len(calls) == before, "an unchanged frame must not pay for the walk"


def test_rate_cap_bounds_a_trigger_storm(rec, clock):
    for i in range(recorder_mod.MAX_RECORDS_PER_SECOND * 3):
        rec.state["screen"] = f"screen{i}"
        rec.request("storm")
        clock.run()
    assert len(lines(rec)) == recorder_mod.MAX_RECORDS_PER_SECOND


def test_rate_cap_recovers_after_the_window_passes(rec, clock):
    for i in range(recorder_mod.MAX_RECORDS_PER_SECOND):
        rec.state["screen"] = f"a{i}"
        rec.request("storm")
        clock.run()
    clock.boottime += 2.0
    rec.state["screen"] = "later"
    rec.request("after")
    clock.run()
    assert len(lines(rec)) == recorder_mod.MAX_RECORDS_PER_SECOND + 1


def test_nothing_is_observed_until_the_ui_has_settled(rec, clock, monkeypatch):
    """Regression: read the picture once it has stopped moving, not before.

    `HomePage.change_mode` defers through Clock and re-schedules while the servo
    is moving, and kv rules that resize a container lay out on a later frame.
    Reading immediately recorded a take-up banner 30 px outside its own
    container, and ELS widgets still mounted after a switch to DRO -- neither
    reproducible by a correctly settled replay, so the guard cried drift on
    every frame.
    """
    assert recorder_mod.SETTLE_SECONDS > 0
    delays = []
    real_schedule = clock.schedule_once
    clock.schedule_once = lambda cb, t=0: (delays.append(t), real_schedule(cb, t))[1]

    rec.request("change")
    assert delays == [recorder_mod.SETTLE_SECONDS], \
        "capture must be deferred by a settle delay, not run inline"
    clock.run()
    assert len(lines(rec)) == 1


def test_values_and_tree_are_read_at_the_same_instant(rec, clock, monkeypatch):
    """They describe one picture, or the drift guard means nothing."""
    order = []
    monkeypatch.setattr(recorder_mod.schema_mod, "snapshot",
                        lambda app, schema: order.append("snapshot") or dict(rec.state))
    monkeypatch.setattr(recorder_mod.digest_mod, "subtree_digests",
                        lambda app: order.append("digest") or {"all": 1})

    rec.request("change")
    clock.run()
    # Each settle probe reads both, in that order, with no frame boundary
    # between them -- so the pair always describes one moment.
    assert order[0::2] == ["snapshot"] * (len(order) // 2)
    assert order[1::2] == ["digest"] * (len(order) // 2)


def test_a_moving_ui_is_probed_until_it_settles(rec, clock, monkeypatch):
    """Regression: record a settled frame, do not guess a delay long enough.

    Geometry keeps moving after property values stop, and
    ``HomePage.change_mode`` re-schedules itself while the servo is moving.
    """
    moving = iter([{"all": 1}, {"all": 2}, {"all": 3}, {"all": 3}, {"all": 3}])
    monkeypatch.setattr(recorder_mod.digest_mod, "subtree_digests",
                        lambda app: next(moving))
    rec.request("swap")
    clock.run()
    records = lines(rec)
    assert len(records) == 1

    from reflex.uistate import schema as schema_mod
    _schema, _values, digests = schema_mod.decode(records[0]["code"])
    assert digests == {"all": 3}, "the settled tree is the one recorded"


def test_a_never_settling_ui_is_still_recorded(rec, clock, monkeypatch):
    """A storyboard with a hole in it is worse than one with a blurred page."""
    counter = iter(range(1000))
    monkeypatch.setattr(recorder_mod.digest_mod, "subtree_digests",
                        lambda app: {"all": next(counter)})
    rec.request("animating")
    clock.run(max_generations=recorder_mod.MAX_SETTLE_PROBES + 5)
    assert len(lines(rec)) == 1


def test_a_blinking_led_does_not_prevent_settling(rec, clock, monkeypatch):
    """`board.blink` toggles at 4 Hz forever; treating it as motion would mean
    the recorder never saw a settled frame and recorded nothing at all."""
    schema = rec._schema
    volatile = [f.key for f in schema.fields if f.volatile]
    assert "board_blink" in volatile and "status_fps" in volatile
    assert "board_blink" not in schema.stable_keys

    flip = iter([False, True, False, True, False, True])
    def snapshot(app, _schema):
        state = dict(rec.state)
        state["board_blink"] = next(flip)
        return state
    monkeypatch.setattr(recorder_mod.schema_mod, "snapshot", snapshot)
    monkeypatch.setattr(recorder_mod.digest_mod, "subtree_digests",
                        lambda app: {"all": 1})

    rec.request("change")
    clock.run()
    assert len(lines(rec)) == 1, "a blinking led must not block a capture"


def test_records_carry_their_own_schema_id(rec, clock):
    """Every line self-describing is what keeps old captures readable."""
    rec.request("first")
    clock.run()
    record = lines(rec)[0]
    assert record["schema"] == rec._schema.id
    assert record["code"].startswith(f"R{rec._schema.id}.")
    assert record["app"] == "vtest"


def test_recorded_code_decodes_back_to_the_snapshot(rec, clock):
    from reflex.uistate import schema as schema_mod
    rec.request("first")
    clock.run()
    _schema, values, digests = schema_mod.decode(lines(rec)[0]["code"])
    assert values["screen"] == "home"
    assert digests == {"all": 1}


def test_repeated_write_failures_disable_the_recorder(rec, clock, monkeypatch):
    """On a full card this must stop, not log a failure forever."""
    def boom(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(recorder_mod.UiStateRecorder, "_rotate_if_needed",
                        lambda self: None)
    monkeypatch.setattr("builtins.open", boom)

    for i in range(recorder_mod.MAX_WRITE_FAILURES):
        assert rec.enabled
        rec.state["screen"] = f"s{i}"
        rec.request("write")
        clock.run()
    assert not rec.enabled, "recorder must disable itself, not retry forever"


def test_a_capture_exception_never_escapes(rec, clock, monkeypatch):
    monkeypatch.setattr(recorder_mod.schema_mod, "snapshot",
                        lambda app, schema: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    rec.request("explode")
    clock.run()  # must not raise
    assert lines(rec) == []


def test_disabled_by_environment(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("REFLEX_UISTATE", "off")

    class App:
        version = "vtest"

    recorder = UiStateRecorder(App(), path=tmp_path / "uistate.jsonl")
    assert not recorder.enabled
    recorder.request("ignored")
    clock.run()
    assert not (tmp_path / "uistate.jsonl").exists()


def test_rotation_keeps_the_log_bounded(rec, clock, monkeypatch):
    monkeypatch.setattr(recorder_mod, "MAX_BYTES", 200)
    for i in range(20):
        rec.state["screen"] = f"screen{i}"
        rec.request("fill")
        clock.boottime += 1.0
        clock.run()
    rotated = list(rec.path.parent.glob("uistate.jsonl.*"))
    assert rotated, "the log must rotate rather than grow without bound"
    assert len(rotated) <= recorder_mod.MAX_FILES
