"""Does a negative Stop Z / Start Z fit its value box in stop + retract mode?

The value Label in TextHeaderButton sizes its font from the box HEIGHT only
(`self.height / 3`), while the box is `width: self.height` wide -- so a
signed, 4-decimal inch value such as "-12.3456" is wider than the box.
This stands the real app up at 1024x600 in stop + retract mode, writes
several stop_z / retract_z magnitudes (positive and negative) under the real
position_format, and compares each value Label's texture width to its box.
Exit status is non-zero if any value is wider than its box. Also writes a PNG
per case when a real GL window is available.

Run (WSL, with a display):

    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" \\
        ./.venv/bin/python previews/preview_advbar_negative_z.py

Headless without X: set KIVY_WINDOW=mock KIVY_GL_BACKEND=mock (what the
pytest suite does). There is no real window then and the mock GL backend
crashes on any texture, so the script falls back to a MODEL run: it evaluates
the value Label's real `font_size:` expression from
reflex/components/toolbars/text_header_button.kv at the bar's known geometry
(control row 102 px: els_advbar.kv natural_height 128 minus the 26 px strip;
the box is square, the value Label is 0.7 of its height) and measures the text
extent with the real DSEG7 font. It states plainly that it is a model. The
real-app run writes PNGs to $OUT_DIR (default: the temp HOME).
"""
import os
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-negz-")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from kivy.config import Config  # noqa: E402

Config.set("graphics", "width", "1024")
Config.set("graphics", "height", "600")

import reflex  # noqa: E402
from kivy.resources import resource_add_path  # noqa: E402

resource_add_path(os.path.dirname(reflex.__file__))

from kivy.base import EventLoop  # noqa: E402
from kivy.clock import Clock  # noqa: E402

from reflex.app import MainApp  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR", os.environ["HOME"])
MOCK = os.environ.get("KIVY_WINDOW") == "mock"
# Inches (the machine's unit): "{:+0.4f}" -> "-12.3456". Sign + 4 decimals is
# the widest realistic string; magnitudes run from small to a long Z travel.
VALUES = [0.0125, -0.0125, 1.5, -1.5, 12.3456, -12.3456, 99.9999, -99.9999]

def _model_run():
    """Headless: kv font_size expression x DSEG7 extent at the real geometry."""
    import re
    from kivy.core.text import Label as CoreLabel
    dp = float   # density 1.0 at 1024x600; kivy.metrics.dp needs a window

    kv_path = os.path.join(os.path.dirname(reflex.__file__), "components",
                           "toolbars", "text_header_button.kv")
    kv = open(kv_path).read()
    # The value Label is the one whose font_name is app.theme.font_seg.
    block = kv.split("font_name: app.theme.font_seg", 1)[1]
    expr = re.search(r"font_size:\s*(.+)", block).group(1).strip()
    print(f"MODEL RUN (no real render): font_size expression = {expr}")
    box_w = 102.0                 # control row height; box is square
    label_h = 0.7 * box_w         # value Label is size_hint_y 0.7
    font = os.path.join(os.path.dirname(reflex.__file__),
                        "fonts", "DSEG7Classic-Regular.ttf")
    fmt = "{:+0.4f}"              # imperial position_format
    for v in VALUES:
        # display_value strips "+" only (TextHeaderButton.__init__ / property)
        text = fmt.format(v).replace("+", "")
        fs = eval(expr, {"min": min, "max": max, "len": len, "dp": dp},
                  {"self": type("S", (), {"height": label_h, "width": box_w,
                                          "text": text})()})
        cl = CoreLabel(text=text, font_name=font, font_size=fs)
        tw = cl.get_extents(text)[0]
        ok = tw <= box_w - 4   # the recessed field is 2 px in from each side
        RESULTS.append(ok)
        print(f"  {text:>9}  font_size={fs:5.1f}  text.x={tw:4d}  "
              f"recess.w={box_w - 4:.0f}  "
              f"{'ok' if ok else 'WIDER THAN BOX by %d' % (tw - box_w + 4)}")


if MOCK:
    RESULTS = []
    _model_run()
    bad = RESULTS.count(False)
    print(f"{len(RESULTS)} measurements, {bad} wider than their box")
    raise SystemExit(1 if bad else 0)

app = MainApp()
RESULTS = []
FAILED = []


def _settle(n=6):
    for _ in range(n):
        EventLoop.idle()


def _find(pred, root=None):
    root = root or app.root
    if pred(root):
        return root
    for child in root.children:
        got = _find(pred, child)
        if got is not None:
            return got
    return None


def _bar():
    return app.manager.get_screen("home").els_bar.ids.get("advanced_bar") \
        or _find(lambda w: type(w).__name__ == "ElsAdvancedBar")


def _value_label(btn):
    # The second child Label (recessed value field); children are reversed.
    labels = [c for c in btn.children if type(c).__name__ == "Label"]
    return [lb for lb in labels if lb.text == btn.display_value][0]


def _measure(tag):
    bar = _bar()
    for name, attr in (("btn_stop_z", "stop_z"), ("btn_start_z", "retract_z")):
        btn = bar.ids[name]
        lb = _value_label(btn)
        tw, w = lb.texture_size[0], lb.width
        ok = tw <= w
        RESULTS.append(ok)
        print(f"  {tag:<10} {name:<12} text={lb.text!r:<11} "
              f"font_size={lb.font_size:5.1f} texture.x={tw:4d} "
              f"box.w={w:5.1f}  {'ok' if ok else 'WIDER THAN BOX by %d' % (tw - w)}")


def _capture(_dt):
    try:
        bar = _bar()
        assert bar is not None, "ElsAdvancedBar is not mounted"
        bar.enable_wizard = False
        bar.enable_retract = True
        _settle()
        uic = app.els_uic
        assert uic.retract_enabled and not uic.wizard_enabled, "mode did not take"
        print(f"window {list(app.root.size)} bar h={round(bar.height)} "
              f"format={app.formats.position_format!r}")
        assert list(app.root.size) == [1024, 600], "not rendering at 1024x600"
        for v in VALUES:
            uic.stop_z = v
            uic.retract_z = v
            uic.stop_z_valid = True
            uic.retract_z_valid = True
            _settle()
            # Gate: the label really shows the value we wrote.
            shown = bar.ids["btn_stop_z"].display_value
            want = app.formats.position_format.format(v).replace("+", "")
            assert shown == want, f"label shows {shown!r}, wanted {want!r}"
            tag = f"{v:+.4f}"
            _measure(tag)
            if not MOCK:
                out = os.path.join(OUT_DIR, f"advbar_z_{tag}.png")
                app.root.export_to_png(out)
                app.root.export_to_png(out)
                print("  WROTE", out)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        FAILED.append(e)
    app.stop()


def _arm(_dt):
    app.use_case = "lathe"
    app.formats.current_format = "IN"
    app.manager.goto("home")
    app.set_mode(2)
    app.manager.get_screen("home").els_bar.enable_advanced = True
    Clock.schedule_once(_capture, 1.5)


Clock.schedule_once(_arm, 2.0)
app.run()

if FAILED:
    raise SystemExit(f"preview failed to run: {FAILED[0]!r}")
if not RESULTS:
    raise SystemExit("no measurements taken")
bad = RESULTS.count(False)
print(f"{len(RESULTS)} measurements, {bad} wider than their box")
raise SystemExit(1 if bad else 0)
