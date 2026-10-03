"""Boot the real app and check the status bar's version stays on ONE line,
in the real font, at the machine's 1024x600 -- and write a crop of it.

WHY (2026-09-25). On the lathe the version read v1.2.0rc7* -- the package
spelling plus an editable-install mark -- 90 px of text in a 92 dp box at
16 px, and it wrapped onto two lines. It now shows the tag spelling
(v1.2.0-rc.7, 99 px at 16) in an AutoSizeLabel that shrinks to fit. The unit
tests check the fit arithmetic against a model of the font; this checks the
font itself, which only renders with a window.

Alpha tags (integration builds, v1.3.0-alpha.12) would shrink to 10 px
in full, so the status bar shows them as -a.N (release_version.status_label).
Each alpha here must render at least as large as the rc of the same numbers.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" \\
        ./.venv/bin/python previews/preview_version_fit.py
"""
import os
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-version-fit-")
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
from kivy.uix.label import Label  # noqa: E402

from reflex.app import MainApp  # noqa: E402
from reflex.components.widgets.auto_size_button import (AutoSizeLabel,  # noqa: E402
                                                        measure_text_width)
from reflex.utils import release_version  # noqa: E402

VERSIONS = ["v1.2.0", "v1.2.0-rc.7", "v1.2.0-rc.12", "v1.2.10-rc.12",
            "v1.2.0-alpha.7", "v1.2.0-alpha.12", "v1.2.10-alpha.12"]
FAILED = []
app = MainApp()


def _find_version_label():
    shown = release_version.status_label(app.version)
    for w in app.root.walk(restrict=False):
        if isinstance(w, AutoSizeLabel) and w.text == shown:
            return w
    raise AssertionError(f"no AutoSizeLabel showing {shown!r} (app.version {app.version!r})")


def _check(_dt):
    fonts = {}
    try:
        for v in VERSIONS:
            app.version = v
            for _ in range(4):
                EventLoop.idle()
            lab = _find_version_label()
            # Lines, measured the way the label wraps: the same text, font and
            # size laid out at the label's width with unlimited height. (The
            # label's own texture is always its box, text_size = size.)
            def height(text):
                probe = Label(text=text, font_name=lab.font_name, font_size=lab.font_size,
                              text_size=(lab.width, None))
                probe.texture_update()
                return probe.texture_size[1]
            one_line = height(lab.text) <= height("0")
            natural = measure_text_width(lab.text, lab.font_name, lab.font_size)
            fits = natural <= lab.width
            fonts[v] = lab.font_size
            rc = fonts.get(v.replace("-alpha.", "-rc.")) if "-alpha." in v else None
            as_large = rc is None or lab.font_size >= rc - 0.01
            print(f"{v:16s} shown {lab.text:13s} font {lab.font_size:5.2f}  text {natural:3d} px  "
                  f"box {lab.width:.0f}  {'ONE LINE' if one_line else 'WRAPPED'}  "
                  f"{'fits' if fits else 'OVERFLOWS'}"
                  + ("" if rc is None else f"  {'>=' if as_large else 'SMALLER THAN'} its rc ({rc:.2f})"))
            if not (one_line and fits and as_large):
                FAILED.append(v)
            # Crop: the status bar's left end, where the dogleg and version sit.
            out = os.path.join(os.path.dirname(__file__),
                               f"preview_version_fit_{v.replace('.', '_')}.png")
            lab.parent.export_to_png(out)
    except Exception as e:  # noqa: BLE001 - preview script, want the traceback
        import traceback
        traceback.print_exc()
        FAILED.append(str(e))
    app.stop()


Clock.schedule_once(_check, 3)
app.run()
print("VERDICT:", "FAIL " + ", ".join(FAILED) if FAILED else "OK -- every version on one line and inside the box")
raise SystemExit(1 if FAILED else 0)
