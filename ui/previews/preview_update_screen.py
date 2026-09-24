"""Headless PNGs + layout assertions for the Software Update screen.

WHY THIS EXISTS. The first real in-app update (v1.2.0-rc.4, on the lathe
2026-09-19) worked, and showed four things no unit test could see at the
machine's 1024x600:
    U1  the "DO NOT POWER OFF THE MACHINE" line ran off the right edge
    U2  the Update Status box -- what the operator watches for the whole
        update -- sat mostly below the fold
    U3  the "Offer pre-releases" toggle reset to off on every visit
    U4  turning it on showed nothing new until Refresh was tapped
U1 and U2 are asserted here against the REAL widget tree; U3/U4 are checked
through the real app's Device-0 dispatcher as well as in the unit tests. The
release list is stubbed (no network); nothing is installed or flashed.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_update_screen.py
"""
import os
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-update-preview-")
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
from kivy.core.window import Window  # noqa: E402
from kivy.uix.label import Label  # noqa: E402
from kivy.uix.scrollview import ScrollView  # noqa: E402
from kivy.uix.textinput import TextInput  # noqa: E402

from reflex.app import MainApp  # noqa: E402
from reflex.components.screens.update_screen import UpdateScreen  # noqa: E402
from reflex.utils.updater import Release  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
SCRATCH = os.path.join(OUT_DIR, "_update_scratch.png")

RESULTS = []
FAILED = []
app = MainApp()


def _release(tag, pre):
    return Release(tag=tag, prerelease=pre,
                   firmware_url=f"https://example/{tag}/app.bin",
                   firmware_name=f"reflex-app-{tag.lstrip('v')}.bin")


CATALOGUE = [_release("v1.2.0-rc.4", True), _release("v1.2.0-rc.3", True),
             _release("v1.1.0", False)]
# The status lines the lathe showed on 2026-09-19, up to the flash.
STATUS = [
    "Fetching releases from GitHub.",
    "Updating v1.2.0-rc.3 -> v1.2.0-rc.4. Both the controller firmware and this UI will be replaced.",
    "Preparing v1.2.0-rc.4.",
    "Python environment is writable: /opt/reflex-venv",
    "uv: /usr/local/bin/uv",
    "Fetching tags from https://github.com/Funkenjaeger/reflex.git.",
    "v1.2.0-rc.4 UI expects register protocol version 11 (this UI: 11).",
    "Downloading reflex-app-1.2.0-rc.4.bin.",
    "Firmware image: rev 1dfa05c, 44940 bytes.",
    "Checkout now: integration at 22027314ec0a.",
    "Controller before: application rev a71ee51 protocolVersion 11.",
    "Flashing v1.2.0-rc.4 firmware. DO NOT POWER OFF THE MACHINE.",
]


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


def check(label, ok, detail):
    RESULTS.append((label, bool(ok), detail))


def rect(w):
    x, y = w.to_window(w.x, w.y)
    return x, y, x + w.width, y + w.height


def on_screen(w):
    x0, y0, x1, y1 = rect(w)
    return (x0 >= -1 and y0 >= -1 and x1 <= Window.width + 1 and y1 <= Window.height + 1,
            f"window rect ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) in {Window.width}x{Window.height}")


def visible_in(w, scroller):
    """Wholly inside the scroller's viewport (what the operator can see)."""
    x0, y0, x1, y1 = rect(w)
    sx0, sy0, sx1, sy1 = rect(scroller)
    return (y0 >= sy0 - 1 and y1 <= sy1 + 1,
            f"widget y {y0:.0f}-{y1:.0f}, viewport y {sy0:.0f}-{sy1:.0f}")


def capture(_dt):
    try:
        screen = app.manager.get_screen("update")
        # The fetch is async and this preview runs no asyncio loop, so hand
        # the screen what a fetch returns, exactly as refresh_releases does.
        screen._catalogue = {r.tag: r for r in CATALOGUE}
        screen._set_releases()
        settle()

        # U3/U4: the toggle is saved on the real Device-0 dispatcher, and
        # turning it on shows pre-releases from the catalogue already held.
        screen.allow_experimental = False
        settle()
        check("toggle off: finals only", screen.releases == ["v1.1.0"], str(screen.releases))
        screen.allow_experimental = True
        settle()
        check("toggle on: pre-releases at once, no refresh",
              screen.releases[:1] == ["v1.2.0-rc.4"], str(screen.releases))
        check("toggle saved to Device-0", app.device.offer_prereleases is True,
              f"device.offer_prereleases={app.device.offer_prereleases}")
        screen.allow_experimental = False
        app.device.offer_prereleases = True   # as saved by an earlier visit
        screen.on_pre_enter()
        check("toggle restored on entry", screen.allow_experimental is True,
              f"allow_experimental={screen.allow_experimental}")
        shot("update_rest")

        # U1/U2: the busy state, exactly as _do_install sets it up, without
        # starting an install.
        screen.status = "\n".join(STATUS) + "\n"
        screen.busy = True
        settle()
        if hasattr(screen, "_scroll_to_status"):
            screen._scroll_to_status()
        settle()
        # Found by what they are, not by id, so the same checks run against
        # the pre-2026-09-19 screen (which had no ids) for the seen-red.
        warn = next(w for w in screen.walk() if isinstance(w, Label)
                    and "DO NOT POWER OFF" in (w.text or ""))
        box = next(w for w in screen.walk() if isinstance(w, TextInput))
        scroller = next(w for w in screen.walk() if isinstance(w, ScrollView))
        warn.texture_update()
        tw, th = warn.texture_size
        check("U1 warning text inside its own box",
              tw <= warn.width + 1 and th <= warn.height + 1,
              f"text {tw:.0f}x{th:.0f} in box {warn.width:.0f}x{warn.height:.0f}")
        check("U1 warning inside the window", *on_screen(warn))
        check("U2 status box wholly in view after the scroll", *visible_in(box, scroller))
        check("U2 warning in view too", *visible_in(warn, scroller))

        shot("update_busy")
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
    # No network: an entry refresh would try GitHub.
    UpdateScreen.schedule_refresh_releases = lambda self: None
    app.use_case = "lathe"
    app.manager.goto("update")
    Clock.schedule_once(capture, 2.0)


Clock.schedule_once(arm, 2.0)
app.run()

if FAILED or not RESULTS or not all(ok for _, ok, _ in RESULTS):
    raise SystemExit(f"{__file__}: FAIL")
