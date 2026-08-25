"""End-to-end: record a real session, replay it, and demand zero drift.

MARKED ``render`` AND EXCLUDED FROM THE DEFAULT SUITE. It needs a display and a
real GL context; the repo-wide ``conftest.py`` forces Kivy's mock backends for
everything else, and building the real widget tree under those segfaults in
Kivy's texture path. Run it explicitly:

    xvfb-run -a -s "-screen 0 1024x600x24" uv run pytest -m render

This is the test that would have caught every bug the drift guard found while
this feature was being built: a banner recorded mid-reflow, ELS widgets still
mounted after a mode swap, an fps value truncated to the wrong integer, the
spindle readout and blink phase replaying from live state instead of the
capture. None of them were visible to a unit test; all of them were obvious the
moment a real frame was replayed and compared.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.render

REPO = Path(__file__).resolve().parents[2]
SESSION = REPO / "previews" / "preview_uistate_session.py"
REPLAY = REPO / "scripts" / "replay_ui_state.py"
STORYBOARD = REPO / "scripts" / "storyboard.py"
TIMEOUT = 600


def _run(script, *args, env=None):
    full = dict(os.environ)
    # The repo-wide conftest sets these for the SUITE, and they are inherited by
    # anything we spawn. A child that renders must not get them: under the mock
    # backends there is no GL context, the app never builds a widget tree, and
    # the subprocess dies with no capture and nothing obviously wrong.
    full.pop("KIVY_GL_BACKEND", None)
    full.pop("KIVY_WINDOW", None)
    full.setdefault("SDL_AUDIODRIVER", "dummy")
    full.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    full.update(env or {})
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, timeout=TIMEOUT, env=full, cwd=str(REPO))


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    if not os.environ.get("DISPLAY"):
        pytest.skip("needs a display; run under xvfb-run")
    out = tmp_path_factory.mktemp("uistate")
    result = _run(SESSION, env={"OUTDIR": str(out)})
    jsonl = out / "uistate.jsonl"
    assert jsonl.exists(), f"no capture written\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    return jsonl


def test_a_session_records_several_distinct_frames(session):
    records = [json.loads(line) for line in
               session.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) >= 5, "the scripted session should produce several frames"
    assert len({r["code"] for r in records}) == len(records), \
        "identical snapshots must be suppressed, not recorded twice"
    for record in records:
        assert record["schema"] == 1
        assert record["code"].startswith("R1.")
        # Small enough that a day at the lathe is kilobytes, not gigabytes.
        assert len(record["code"]) < 2000


def test_every_frame_replays_without_drift(session, tmp_path):
    """The whole point: a replayed frame must match what was really on screen."""
    out = tmp_path / "story"
    result = _run(REPLAY, str(session), "--out", str(out),
                  "--settle", "0.6", "--strict")
    assert result.returncode == 0, (
        "replay reported drift or an error -- a field is missing from the "
        f"schema.\n{result.stdout[-4000:]}\n{result.stderr[-3000:]}")

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest
    for entry in manifest:
        assert not entry["drift"], f"frame {entry['index']} drifted: {entry['drift']}"
        assert not entry["error"], f"frame {entry['index']}: {entry['error']}"
        png = out / entry["png"]
        assert png.exists() and png.stat().st_size > 5000


def test_replayed_frames_are_visually_distinct(session, tmp_path):
    """Guards against a replay that silently renders the same screen every time."""
    from PIL import Image, ImageChops

    out = tmp_path / "story"
    assert _run(REPLAY, str(session), "--out", str(out),
                "--settle", "0.6").returncode == 0
    frames = sorted(out.glob("frame_*.png"))
    assert len(frames) >= 5

    previous = None
    changed = 0
    for path in frames:
        image = Image.open(path).convert("RGB")
        if previous is not None and ImageChops.difference(image, previous).getbbox():
            changed += 1
        previous = image
    assert changed >= len(frames) - 2, \
        "consecutive frames should differ -- the session changes the screen"


def test_storyboard_is_one_self_contained_file(session, tmp_path):
    out = tmp_path / "story"
    assert _run(REPLAY, str(session), "--out", str(out),
                "--settle", "0.6").returncode == 0
    assert _run(STORYBOARD, str(out)).returncode == 0

    page = (out / "index.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in page, "images must be inlined"
    assert "src=\"frame_" not in page, "no external image references"
    assert "all faithful" in page
