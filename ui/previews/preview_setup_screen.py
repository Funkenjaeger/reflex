"""Headless PNGs + layout assertions for the Setup screen's USB/gist surfaces.

WHY THIS EXISTS. The Export/Import buttons and the gist row went to the lathe
on 2026-09-17 and three defects were visible on the first real use, at the
machine's 1024x600 -- none of which any unit test could see:
    S1  the export status line ("Exported commissioning bundle to /media/...")
        ran off the edge of the screen
    S2  the gist controls drew over their neighbours (on a build with no OAuth
        client id -- i.e. every build today -- the "not configured" notice is
        shown at 1.8x font in a 64 dp box)
    S3  the import dialog showed the raw UTC ISO stamp ("2026-09-18T00:39:09
        +00:00" for a capture made at 20:39 local), and "Firmware: None"
Each has an assertion below made against the REAL widget tree at 1024x600, and
the run ends in a PASS/FAIL table that exits non-zero on any FAIL. Same
contract as preview_walkthrough_shots.py: nothing on screen is typed in here --
the status and dialog text come out of SetupScreen's own methods. Only the USB
boundary (usb.export_bundle / usb.find_bundles) is stubbed.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_setup_screen.py
"""
import os
import tempfile
from pathlib import Path

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-setup-preview-")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from kivy.config import Config  # noqa: E402

Config.set("graphics", "width", "1024")
Config.set("graphics", "height", "600")

import reflex  # noqa: E402
from kivy.resources import resource_add_path  # noqa: E402

resource_add_path(os.path.dirname(reflex.__file__))

import yaml  # noqa: E402
from kivy.base import EventLoop  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.uix.label import Label  # noqa: E402

from reflex.app import MainApp  # noqa: E402
from reflex.utils import commissioning_bundle, usb  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
SCRATCH = os.path.join(OUT_DIR, "_setup_scratch.png")

# The exact path the lathe wrote on 2026-09-17 -- the width that clipped.
EXPORT_PATH = Path("/media/sda1-3/reflex-commissioning-elspi-20260918T003909Z.yaml")
CAPTURE_TS = "2026-09-18T00:39:09+00:00"

RESULTS = []
FAILED = []
app = MainApp()


def settle(n=14):
    for _ in range(n):
        EventLoop.idle()


def shot(name):
    settle()
    target = os.path.join(OUT_DIR, f"{name}.png")
    for path in (SCRATCH, target):
        if os.path.exists(path):
            os.remove(path)
    got = Window.screenshot(name=SCRATCH)
    if got and os.path.exists(got):
        os.remove(got)
    settle(4)
    got = Window.screenshot(name=SCRATCH)
    os.replace(got, target)
    print(f"WROTE {target}")


def check(label, ok, detail):
    RESULTS.append((label, bool(ok), detail))


def fits(lbl: Label):
    """Rendered text is inside the label's own box, both axes."""
    lbl.texture_update()
    tw, th = lbl.texture_size
    return tw <= lbl.width + 1 and th <= lbl.height + 1, f"text {tw:.0f}x{th:.0f} in box {lbl.width:.0f}x{lbl.height:.0f}"


def on_screen(w):
    x, y = w.to_window(w.x, w.y)
    return x >= -1 and y >= -1 and x + w.width <= Window.width + 1 and y + w.height <= Window.height + 1


def overlaps(a, b):
    ax, ay = a.to_window(a.x, a.y)
    bx, by = b.to_window(b.x, b.y)
    return not (ax + a.width <= bx + 1 or bx + b.width <= ax + 1 or
                ay + a.height <= by + 1 or by + b.height <= ay + 1)


def layout_checks(screen, tag):
    box = screen.children[0]
    rows = [w for w in box.children if w.height > 0 and w.opacity > 0]
    clashes = [(type(a).__name__, type(b).__name__)
               for i, a in enumerate(rows) for b in rows[i + 1:] if overlaps(a, b)]
    check(f"{tag}: S2 no row overlaps another", not clashes, f"overlapping: {clashes}" if clashes else f"{len(rows)} rows")
    offscreen = [type(w).__name__ for w in rows if not on_screen(w)]
    check(f"{tag}: S2 every row is on screen", not offscreen, f"off: {offscreen}" if offscreen else "ok")
    for wid in ("gist_code_label", "gist_note_label", "status_label"):
        w = screen.ids.get(wid)
        if w is None or w.height == 0 or w.opacity == 0 or not w.text:
            continue
        ok, detail = fits(w)
        check(f"{tag}: {wid} text fits its box", ok and on_screen(w), detail)


def inside(child, parent):
    cx, cy = child.to_window(child.x, child.y)
    px, py = parent.to_window(parent.x, parent.y)
    return (cx >= px - 1 and cy >= py - 1 and
            cx + child.width <= px + parent.width + 1 and cy + child.height <= py + parent.height + 1)


def capture_setup(_dt):
    try:
        setup = app.manager.get_screen("setup_screen")
        shot("setup_grid")
        grid = setup.ids.grid
        outside = [b.text for b in grid.children if b.opacity > 0 and not inside(b, grid)]
        check("S2 every Setup button is inside the grid", not outside, f"outside: {outside}" if outside else f"{len(grid.children)} buttons")
        texts = {b.text for b in grid.children}
        check("S2 backup controls are off the Setup menu", "Backup" in texts and not texts & {
            "Export to USB", "Import from USB", "Restore from gist"}, sorted(texts))
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAILED.append(e)
    app.manager.goto("backup")
    Clock.schedule_once(capture, 1.0)


def capture(_dt):
    try:
        screen = app.manager.get_screen("backup")

        shot("backup_idle")
        layout_checks(screen, "idle")

        # What the lathe shows today: a build with no OAuth client id.
        from reflex.utils import gist_sync
        real_is_configured = gist_sync.is_configured
        gist_sync.is_configured = lambda: False
        try:
            screen.refresh_gist_state()
        finally:
            gist_sync.is_configured = real_is_configured
        shot("backup_gist_not_configured")
        layout_checks(screen, "not configured")
        check("S2 not-configured notice is in the small note, not the code box",
              screen.gist_note_text == gist_sync.NOT_CONFIGURED_MESSAGE and not screen.gist_code_text,
              repr(screen.gist_note_text))

        # The configured state with a device flow in progress: the tallest the
        # screen gets. Forced on the real properties, not re-typed text.
        screen.gist_configured = True
        screen.gist_note_text = f"Revoke access at {screen.gist_revoke_text}"
        screen.gist_code_text = "WDJB-MJHT\nEnter this at https://github.com/login/device"
        screen._status("Synced to gist 0123456789abcdef0123456789abcdef")
        shot("backup_gist_device_code")
        layout_checks(screen, "device code")
        screen.gist_code_text = ""
        screen.refresh_gist_state()

        real_export = usb.export_bundle
        usb.export_bundle = lambda text, filename: EXPORT_PATH
        try:
            screen.export_to_usb()
        finally:
            usb.export_bundle = real_export
        shot("backup_after_export")
        ok, detail = fits(screen.ids.status_label)
        check("S1 export status line fits the screen", ok and on_screen(screen.ids.status_label), detail)
        check("S1 status line still names the file", EXPORT_PATH.name in screen.status_text, screen.status_text)
        layout_checks(screen, "after export")

        doc = commissioning_bundle.build(fw_rev="43ac7c5")
        doc["meta"]["ts"] = CAPTURE_TS
        tmp = Path(tempfile.mkdtemp()) / EXPORT_PATH.name
        tmp.write_text(yaml.safe_dump(doc, sort_keys=False))
        real_find = usb.find_bundles
        usb.find_bundles = lambda *a, **k: [tmp]
        try:
            screen.import_from_usb()
        finally:
            usb.find_bundles = real_find
        shot("backup_import_confirm")
        msg = screen.import_popup.message
        check("S3 capture time is not the raw UTC ISO string", CAPTURE_TS not in msg and "+00:00" not in msg, msg.splitlines()[1])
        check("S3 firmware shows the revision, not None", "None" not in msg and "43ac7c5" in msg, msg.splitlines()[2])
        # The message label has text_size = its own size, so its texture is
        # CLIPPED to the box and can never "overflow" -- measure the text's
        # natural wrapped height instead. (The first version of this check
        # compared the texture to the box, passed, and the frame showed the
        # first and last lines cut off.)
        lbl = screen.import_popup.ids.lbl_message
        probe = Label(text=msg, font_name=lbl.font_name, font_size=lbl.font_size,
                      halign="center", text_size=(lbl.width, None))
        probe.texture_update()
        need = probe.texture_size[1]
        check("S3 dialog message is not clipped", need <= lbl.height + 1,
              f"text needs {need:.0f}px, box is {lbl.height:.0f}px")
        screen.import_popup._popup.dismiss()

        doc["meta"]["fw"] = None
        tmp.write_text(yaml.safe_dump(doc, sort_keys=False))
        usb.find_bundles = lambda *a, **k: [tmp]
        try:
            screen.import_from_usb()
        finally:
            usb.find_bundles = real_find
        msg = screen.import_popup.message
        check("S3 an old fw-less bundle says so, not None", "None" not in msg, msg.splitlines()[2])
        screen.import_popup._popup.dismiss()
    except Exception as e:  # never let the Kivy clock swallow it
        import traceback
        traceback.print_exc()
        FAILED.append(e)
    finally:
        print("\n==== RESULTS ====")
        for label, ok, detail in RESULTS:
            print(f"{'PASS' if ok else 'FAIL'}  {label}  [{detail}]")
        app.stop()


def arm(_dt):
    app.use_case = "lathe"
    app.manager.goto("setup_screen")
    Clock.schedule_once(capture_setup, 1.0)


Clock.schedule_once(arm, 2.0)
app.run()

if FAILED or not RESULTS or not all(ok for _, ok, _ in RESULTS):
    raise SystemExit(f"{__file__}: FAIL")
