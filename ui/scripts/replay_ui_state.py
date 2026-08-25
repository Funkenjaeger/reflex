"""Replay recorded UI state codes as PNG screenshots.

    # one code
    xvfb-run -a -s "-screen 0 1024x600x24" SDL_AUDIODRIVER=dummy \
        uv run python scripts/replay_ui_state.py --out /tmp/story R1.C5K2...

    # a whole session
    xvfb-run -a -s "-screen 0 1024x600x24" SDL_AUDIODRIVER=dummy \
        uv run python scripts/replay_ui_state.py --out /tmp/story \
            ~/.config/reflex/uistate/uistate.jsonl

THE HEADLESS RECIPE IS ``scripts/capture_readme_screenshots.py``'s, deliberately
and in full: isolated temp HOME before any Kivy import, size forced through
``Config`` before a Window exists, several ``EventLoop.idle()`` passes to let
textures settle, ``export_to_png`` called TWICE because the first export
under-renders, and a composite over black because the export has a transparent
background while the app's real ``clearcolor`` is not. Every one of those was
paid for once already; none is re-learned here.

ONE BOOT REPLAYS THE WHOLE FILE. Kivy takes a couple of seconds to start, so the
frames are applied in sequence against a single running app rather than a
process per frame.

WHAT ``--strict`` IS FOR. Semantic replay cannot prove it reproduced the moment;
the drift digests recorded alongside each code are an independent observation of
what was really on screen (see ``reflex/uistate/digest.py``). A mismatch means
the schema is missing a field -- a bug report, not a warning to live with -- so
``--strict`` exits non-zero when any frame drifts.
"""

import argparse
import json
import os
import sys
import tempfile

# Must precede every Kivy/reflex import: HOME decides where dispatchers read
# their YAML from, and replaying against a developer's real config would apply
# their axes and theme over the captured ones.
os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-replay-home-")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
# Never record while replaying: the recorder would append the frames it is
# reproducing back into a fresh log.
os.environ["REFLEX_UISTATE"] = "off"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="a UI state code, or a path to a .jsonl")
    parser.add_argument("--out", default="replay", help="output directory")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if any frame drifts from its capture")
    parser.add_argument("--limit", type=int, default=0, help="stop after N frames")
    parser.add_argument("--dump-tree", action="store_true",
                        help="write frame_NNNNN.widgets.txt beside each PNG, to "
                             "diff against a capture-side REFLEX_UISTATE_VERBOSE "
                             "dump when chasing a drift report")
    parser.add_argument("--settle", type=float, default=0.35,
                        help="seconds to let a mode/screen swap mount (HomePage."
                             "change_mode defers through Clock)")
    return parser.parse_args(argv)


def load_records(source):
    """Return [{ts, ev, code}, ...] from a JSONL path or a single bare code."""
    if os.path.exists(source):
        records = []
        with open(source, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"skipping {source}:{number}: {e}", file=sys.stderr)
        return records
    return [{"ts": "", "ev": "cli", "code": source.strip()}]


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    records = load_records(args.source)
    if args.limit:
        records = records[:args.limit]
    if not records:
        print("nothing to replay", file=sys.stderr)
        return 2

    import reflex
    from kivy.resources import resource_add_path
    resource_add_path(os.path.dirname(reflex.__file__))

    from reflex.uistate import schema as schema_mod
    import reflex.uistate.schema_v1  # noqa: F401 - registers schema 1

    # Size the Window from the first frame, before Kivy builds one. A Field
    # cannot do this: by the time fields are applied the Window already exists.
    _schema, head, _digests = schema_mod.decode(records[0]["code"])
    width = int(head.get("win_w") or 1024)
    height = int(head.get("win_h") or 600)

    from kivy.config import Config
    Config.set("graphics", "width", str(width))
    Config.set("graphics", "height", str(height))

    from kivy.base import EventLoop
    from kivy.clock import Clock
    from PIL import Image

    from reflex.uistate import digest as digest_mod
    from reflex.app import MainApp

    os.makedirs(args.out, exist_ok=True)
    app = MainApp()
    results = []

    def composite_over_black(path):
        image = Image.open(path).convert("RGBA")
        background = Image.new("RGBA", image.size, (0, 0, 0, 255))
        Image.alpha_composite(background, image).convert("RGB").save(path)

    def replay(index):
        if index >= len(records):
            _write_manifest(args.out, results)
            app.stop()
            return
        record = records[index]
        name = f"frame_{index:05d}.png"
        entry = {"index": index, "png": name, "ts": record.get("ts", ""),
                 "ev": record.get("ev", ""), "code": record.get("code", ""),
                 "drift": [], "error": ""}
        try:
            schema, values, recorded_digests = schema_mod.decode(record["code"])
            if values.get("app_version") and values["app_version"] != app.version:
                entry["note"] = (f"captured under {values['app_version']}, "
                                 f"replaying under {app.version}")
            failed = schema_mod.apply(app, values, schema)
            if failed:
                entry["error"] = "could not apply: " + ", ".join(failed)
            entry["values"] = values
            entry["recorded_digests"] = recorded_digests
        except Exception as e:  # noqa: BLE001 - one bad frame must not end the run
            entry["error"] = f"{type(e).__name__}: {e}"
            results.append(entry)
            Clock.schedule_once(lambda _dt: replay(index + 1), 0)
            return

        # A mode or screen swap mounts on a later frame (HomePage.change_mode
        # defers through Clock and waits for servo speed), so settle before
        # measuring or exporting anything.
        Clock.schedule_once(lambda _dt: shoot(index, entry), args.settle)

    def shoot(index, entry):
        try:
            for _ in range(6):
                EventLoop.idle()
            observed = digest_mod.subtree_digests(app)
            entry["drift"] = digest_mod.compare(
                entry.get("recorded_digests", {}), observed)
            if args.dump_tree:
                dump = os.path.join(args.out, entry["png"].replace(
                    ".png", ".widgets.txt"))
                with open(dump, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(digest_mod.describe_tree(app)) + "\n")
            path = os.path.join(args.out, entry["png"])
            # The first export under-renders (one tile only); export twice.
            app.root.export_to_png(path)
            app.root.export_to_png(path)
            composite_over_black(path)
            status = "DRIFT " + ",".join(entry["drift"]) if entry["drift"] else "ok"
            print(f"[{index:05d}] {entry['ev']:<22} {status}")
        except Exception as e:  # noqa: BLE001 - keep going through a bad frame
            entry["error"] = f"{type(e).__name__}: {e}"
            print(f"[{index:05d}] FAILED {e}", file=sys.stderr)
        results.append(entry)
        Clock.schedule_once(lambda _dt: replay(index + 1), 0)

    def arm(_dt):
        app.replay_mode = True
        replay(0)

    Clock.schedule_once(arm, 2.0)
    app.run()

    drifted = [r for r in results if r["drift"]]
    errored = [r for r in results if r["error"]]
    print(f"\n{len(results)} frame(s) -> {args.out}; "
          f"{len(drifted)} drifted, {len(errored)} errored")
    if args.strict and (drifted or errored):
        return 1
    return 0


def _write_manifest(out_dir, results):
    """Everything the storyboard needs, so it never re-decodes a code."""
    slim = []
    for entry in results:
        item = {k: entry[k] for k in
                ("index", "png", "ts", "ev", "code", "drift", "error")
                if k in entry}
        item["note"] = entry.get("note", "")
        item["values"] = entry.get("values", {})
        slim.append(item)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as h:
        json.dump(slim, h, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
