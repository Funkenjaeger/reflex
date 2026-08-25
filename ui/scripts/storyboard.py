"""Turn a replayed session into a single self-contained HTML contact sheet.

    uv run python scripts/storyboard.py /tmp/story --out /tmp/story/index.html

Reads the ``manifest.json`` that ``replay_ui_state.py`` writes, so it never
decodes a code itself -- there is one decoder, in ``reflex/uistate``, and this
is a presentation layer over its output.

Images are inlined as data URIs so the result is ONE file: it can be attached to
an issue, mailed, or opened from a USB stick at the machine, which is where a
storyboard of an incident is actually wanted.

FOR A VIDEO, there is deliberately no code here. The PNGs are numbered in time
order, so ffmpeg already does it:

    ffmpeg -framerate 2 -i frame_%05d.png -c:v libx264 -pix_fmt yuv420p walk.mp4

The real timestamps are in the manifest for anyone wanting a variable-rate cut.
"""

import argparse
import base64
import html
import json
import os
import sys

# Fields whose change is worth calling out under a frame. The rest are still in
# the code; this is the "what moved" summary, not the record.
INTERESTING = (
    "screen", "mode", "theme", "fmt_units", "uic_state", "uic_instruction",
    "uic_action_text", "uic_alarm_text", "uic_active_input",
    "uic_takeup_warning", "uic_reframe_message", "els_mode_name",
    "els_feed_name", "els_advanced", "board_connected", "spindle_display_rpm",
)


def summarise(previous, current):
    """Human-readable deltas between two frames."""
    if not previous:
        return []
    return [f"{key}: {previous.get(key)!r} -> {current.get(key)!r}"
            for key in INTERESTING if previous.get(key) != current.get(key)]


def data_uri(path):
    with open(path, "rb") as handle:
        return "data:image/png;base64," + base64.b64encode(handle.read()).decode()


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Reflex UI storyboard</title>
<style>
  :root { color-scheme: dark; }
  body { font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; margin: 2rem;
         background: #14181c; color: #dfe6ee; }
  h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
  .meta { color: #8a99a8; margin-bottom: 2rem; }
  .frame { display: grid; grid-template-columns: minmax(0,3fr) minmax(0,2fr);
           gap: 1.5rem; padding: 1.25rem 0; border-top: 1px solid #2a323b; }
  img { width: 100%; height: auto; border: 1px solid #2a323b; border-radius: 6px; }
  .n { color: #8a99a8; font-variant-numeric: tabular-nums; }
  .ev { font-weight: 600; color: #40e0ed; }
  .drift { display: inline-block; background: #6b1d1d; color: #ffd7d7;
           padding: .1rem .5rem; border-radius: 4px; font-size: .8rem; }
  .ok { display: inline-block; background: #1d4a2a; color: #cdf3d8;
        padding: .1rem .5rem; border-radius: 4px; font-size: .8rem; }
  ul { margin: .5rem 0; padding-left: 1.1rem; }
  code { font-family: ui-monospace, monospace; font-size: .78rem;
         word-break: break-all; color: #9fb3c8; }
  details { margin-top: .5rem; }
  @media (max-width: 900px) { .frame { grid-template-columns: 1fr; } }
</style>
<h1>Reflex UI storyboard</h1>
<div class="meta">__COUNT__ frame(s)__DRIFT_NOTE__</div>
__FRAMES__
"""

FRAME = """<div class="frame">
  <div><img src="__IMG__" alt="frame __INDEX__"></div>
  <div>
    <div><span class="n">#__INDEX__</span> &middot; <span class="ev">__EVENT__</span>
         &middot; <span class="n">__TS__</span></div>
    <div style="margin:.5rem 0">__STATUS__</div>
    __NOTE____CHANGES__
    <details><summary class="n">code</summary><code>__CODE__</code></details>
  </div>
</div>
"""


def _fill(template, **fields):
    """Placeholder substitution that leaves the CSS braces alone."""
    for key, value in fields.items():
        template = template.replace(f"__{key}__", value)
    return template


def build(out_dir, manifest):
    frames, previous = [], None
    for entry in manifest:
        png = os.path.join(out_dir, entry["png"])
        if not os.path.exists(png):
            continue
        if entry.get("drift"):
            status = ('<span class="drift">DRIFT: '
                      + html.escape(", ".join(entry["drift"])) + "</span>")
        elif entry.get("error"):
            status = '<span class="drift">' + html.escape(entry["error"]) + "</span>"
        else:
            status = '<span class="ok">faithful</span>'

        changes = summarise(previous, entry.get("values", {}))
        changes_html = ""
        if changes:
            changes_html = "<ul>" + "".join(
                f"<li>{html.escape(c)}</li>" for c in changes) + "</ul>"

        note = entry.get("note", "")
        frames.append(_fill(
            FRAME,
            IMG=data_uri(png),
            INDEX=f"{entry['index']:05d}",
            EVENT=html.escape(entry.get("ev", "")),
            TS=html.escape(entry.get("ts", "")),
            STATUS=status,
            NOTE=f'<div class="n">{html.escape(note)}</div>' if note else "",
            CHANGES=changes_html,
            CODE=html.escape(entry.get("code", "")),
        ))
        previous = entry.get("values", {}) or previous

    drifted = sum(1 for e in manifest if e.get("drift"))
    drift_note = (f' &middot; <span class="drift">{drifted} drifted</span>'
                  if drifted else " &middot; all faithful")
    return _fill(PAGE, COUNT=str(len(frames)), DRIFT_NOTE=drift_note,
                 FRAMES="\n".join(frames))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="directory written by replay_ui_state.py")
    parser.add_argument("--out", default=None,
                        help="output HTML (default: DIR/index.html)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    manifest_path = os.path.join(args.dir, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"no manifest.json in {args.dir}; run replay_ui_state.py first",
              file=sys.stderr)
        return 2
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    out = args.out or os.path.join(args.dir, "index.html")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(build(args.dir, manifest))
    print(f"wrote {out} ({os.path.getsize(out) // 1024} kB, "
          f"{len(manifest)} frame(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
