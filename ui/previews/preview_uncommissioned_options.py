"""Headless PNGs + fit checks for the UNCOMMISSIONED options dialog.

WHY THIS EXISTS. The dialog's first version was checked only as text (mock
GL cannot build it), and on the first fresh elspi card (2026-09-26) it
overflowed the lathe's 1024x600 badly. This renders the REAL dialog, in the
real fonts, the way the operator reaches it -- a fresh HOME is an empty config
directory, so the app latches UNCOMMISSIONED on its own, and the dialog is
opened by tapping the strip -- in both themes and both branches, and asserts:

    F1  every line of text fits inside its own widget (no overflow, no clip)
    F2  the dialog is inside the window, and every button inside the dialog
    F3  the button stack does not overlap the text above it
    F4  text-on-fill contrast >= 3:1 for the headline and every button,
        from the theme tokens the kv draws with

``--seen-red`` shrinks the dialog to a size its content cannot fit, and the
same checks must then FAIL; a check that cannot fail proves nothing.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_uncommissioned_options.py [--seen-red]
"""
import os
import sys
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-uncommissioned-preview-")
os.environ.pop("REFLEX_CONFIG_DIR", None)
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

from reflex.app import MainApp  # noqa: E402
from reflex.components.home import uncommissioned_details as details  # noqa: E402
from reflex.components.home.uncommissioned_banner import UncommissionedBanner  # noqa: E402
from reflex.utils import commissioning_state  # noqa: E402
from reflex.utils.paths import config_dir  # noqa: E402

SEEN_RED = "--seen-red" in sys.argv
if SEEN_RED:
    details.SIZE_HINT = (0.45, 0.5)
    details.SIZE_HINT_RESTART = (0.35, 0.3)

OUT_DIR = os.environ.get("OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
SCRATCH = os.path.join(OUT_DIR, "_uncommissioned_scratch.png")
THEMES = ("dark", "light")
MIN_RATIO = 3.0

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
    return target


def check(label, ok, detail):
    RESULTS.append((label, bool(ok), detail))


def rect(w):
    x, y = w.to_window(w.x, w.y)
    return x, y, x + w.width, y + w.height


def inside(inner, outer, slack=1):
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    return (ix0 >= ox0 - slack and iy0 >= oy0 - slack
            and ix1 <= ox1 + slack and iy1 <= oy1 + slack)


def fmt_rect(r):
    return "({:.0f},{:.0f})-({:.0f},{:.0f})".format(*r)


def _lin(c):
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgba):
    r, g, b = rgba[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def banner():
    home = app.manager.get_screen("home")
    return next(w for w in home.walk() if isinstance(w, UncommissionedBanner))


def fit_checks(dialog, tag):
    popup = dialog._popup
    window = (0, 0, Window.width, Window.height)
    check(f"{tag} F2 dialog inside the window", inside(rect(popup), window),
          fmt_rect(rect(popup)))

    for w in dialog.walk(restrict=True):
        if not isinstance(w, Label) or not (w.text or "").strip():
            continue
        w.texture_update()
        tw, th = w.texture_size
        name = (w.text.splitlines()[0])[:34]
        check(f"{tag} F1 text fits: {name!r}",
              tw <= w.width + 1 and th <= w.height + 1,
              f"text {tw:.0f}x{th:.0f} in box {w.width:.0f}x{w.height:.0f}")

    content = rect(dialog)
    for key, button in dialog.buttons.items():
        check(f"{tag} F2 button inside the dialog: {key}",
              inside(rect(button), content), f"{fmt_rect(rect(button))} in {fmt_rect(content)}")

    text_bottom = min(rect(w)[1] for w in (dialog.ids.lbl_headline, dialog.ids.lbl_line)
                      + ((dialog.ids.lbl_note,) if dialog.note else ()))
    stack_top = max(rect(b)[3] for b in dialog.buttons.values())
    check(f"{tag} F3 buttons below the text", stack_top <= text_bottom + 1,
          f"stack top {stack_top:.0f}, text bottom {text_bottom:.0f}")


def contrast_checks(dialog, tag):
    t = app.theme
    check(f"{tag} F4 headline on background",
          ratio(t.text, t.background) >= MIN_RATIO, f"{ratio(t.text, t.background):.2f}:1")
    for option in dialog.options:
        if option.primary:
            r = ratio(t.accent_text, t.accent_bg)
            pair = "accent_text on accent_bg"
        else:
            r = ratio(t.text, t.surface)
            pair = "text on surface"
        check(f"{tag} F4 {option.key}: {pair}", r >= MIN_RATIO, f"{r:.2f}:1")


def run_theme(idx):
    theme = THEMES[idx]
    try:
        app.formats.theme = theme
        settle()
        check(f"{theme}: theme applied", app.theme.name == theme, app.theme.name)

        # The ordinary branch, reached the way the operator reaches it.
        strip = banner()
        check(f"{theme}: the fresh card latched UNCOMMISSIONED",
              strip.active and commissioning_state.dismissal_available(),
              f"active={strip.active} available={commissioning_state.dismissal_available()}")
        opened = []
        real_open = details.open_details
        details.open_details = lambda **kw: opened.append(real_open(**kw)) or opened[-1]
        try:
            strip.on_release()
        finally:
            details.open_details = real_open
        dialog = opened[0]
        settle(30)
        fit_checks(dialog, f"{theme} options")
        contrast_checks(dialog, f"{theme} options")
        print("wrote", shot(f"uncommissioned_options_{theme}"))
        dialog.close()
        settle()
    except Exception as e:  # never let the Kivy clock swallow it
        import traceback
        traceback.print_exc()
        FAILED.append(e)
    if idx + 1 < len(THEMES):
        Clock.schedule_once(lambda _dt: run_theme(idx + 1), 1.0)
    else:
        Clock.schedule_once(run_restart_branch, 1.0)


def run_restart_branch(_dt):
    try:
        # Configuration arriving after the latch, as an import would.
        (config_dir() / "Axis-1.yaml").write_text("axis_name: Z\n")
        check("restart: dismissal withdrawn",
              not commissioning_state.dismissal_available(), "")
        for theme in THEMES:
            app.formats.theme = theme
            settle()
            dialog = details.open_details()
            settle(30)
            check(f"{theme} restart: only OK", list(dialog.buttons) == [details.RESTART_OK],
                  str(list(dialog.buttons)))
            fit_checks(dialog, f"{theme} restart")
            print("wrote", shot(f"uncommissioned_restart_{theme}"))
            dialog.close()
            settle()
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAILED.append(e)
    finally:
        print("\n==== RESULTS" + (" (--seen-red: FAILs expected)" if SEEN_RED else "") + " ====")
        for label, ok, detail in RESULTS:
            print(f"{'PASS' if ok else 'FAIL'}  {label}  [{detail}]")
        fails = sum(1 for _, ok, _ in RESULTS if not ok)
        print(f"\n{len(RESULTS)} checks, {fails} FAIL")
        app.stop()


def arm(_dt):
    app.use_case = "lathe"
    Clock.schedule_once(lambda _dt: run_theme(0), 1.0)


Clock.schedule_once(arm, 2.0)
app.run()

ok = not FAILED and RESULTS and all(ok for _, ok, _ in RESULTS)
if SEEN_RED:
    # The shrunken dialog MUST trip the fit checks, or they cannot see overflow.
    fit_fails = [label for label, ok, _ in RESULTS if not ok and (" F1 " in label or " F2 " in label)]
    print(f"seen-red: {len(fit_fails)} fit checks failed on the shrunken dialog")
    raise SystemExit(0 if fit_fails else f"{__file__}: seen-red did NOT go red")
if not ok:
    raise SystemExit(f"{__file__}: FAIL")
