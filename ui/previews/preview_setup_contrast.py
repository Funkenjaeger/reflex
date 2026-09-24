"""Headless PNGs + text-contrast assertions for EVERY Setup screen, dark AND light.

WHY THIS EXISTS. Reported by Evan at the lathe on 2026-09-19: "most of the
entries under the main Setup > System menu are invisible, at least in dark
mode." No unit test could see it -- every widget was there, laid out, with the
right text; the text was simply drawn in a colour the eye could not find on
its background. So this check reads the PIXELS: it renders each real Setup
screen at the lathe's 1024x600 in each built-in theme, scrolls through the
whole list, and for every visible label, button and text field measures the
contrast of the most contrasting pixel in the text's own box against that
box's dominant (background) colour. Anything under MIN_RATIO fails, and any
failure makes the run exit non-zero.

It began as a System-only check (425bb50) with an info-only --sweep of the
other screens. The sweep found the same class of defect on eight more screens
(OFF toggles, a disabled Backup button dimmed by alpha, unthemed stock
Button/Label/TextInput drawing white on the light theme), so since 2026-09-19
every screen reachable from the Setup menu gates -- the menu itself, its twelve
sub-screens, and the screens those open (one input, one axis, the log viewer,
the colour and font pickers).

Only a few screens are given the state a lathe shows (a status line, a
disabled button, the free-space figure) so that state is measured too.
Everything else -- the kv rules, the theme, the disabled states -- is
production.

A DISABLED row-help "?" icon is no longer drawn at all: Evan chose, on
2026-09-19, to hide the icon on rows that have no help topic rather than fade
it (it was white at 30%, ~1.1:1 in the light theme). Nothing to measure, so
nothing is excluded here any more. An ENABLED help icon is a control and
gates like text.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_setup_contrast.py [--only system,backup]
"""
import os
import sys
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-setup-contrast-")
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
from kivy.factory import Factory  # noqa: E402
from kivy.uix.label import Label  # noqa: E402
from kivy.uix.scrollview import ScrollView  # noqa: E402
from kivy.uix.textinput import TextInput  # noqa: E402
from PIL import Image  # noqa: E402

from reflex.app import MainApp  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
SCRATCH = os.path.join(OUT_DIR, "_setup_contrast_scratch.png")

MIN_RATIO = 3.0          # WCAG large-text floor; every row here is >= 18 px
THEMES = ("dark", "light")


def _arg(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


ONLY = set(filter(None, (_arg("--only") or "").split(",")))

RESULTS = []
INFO = []
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
    return target


def check(label, ok, detail):
    RESULTS.append((label, bool(ok), detail))


# -- contrast arithmetic (WCAG 2.x relative luminance) -----------------------
def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def fmt(c):
    return "(" + ", ".join(f"{v:.2f}" for v in c) + ")"


# -- which widgets carry text, and where is that text ------------------------
def shown(w):
    """Visible in the tree: no zero-opacity ancestor, non-empty box."""
    node = w
    while node is not None and node is not node.parent:
        if getattr(node, "opacity", 1) <= 0:
            return False
        node = node.parent
    return w.width > 2 and w.height > 2


def viewport(w):
    """Window rect the widget can be seen in: its innermost ScrollView, else
    the whole window."""
    node = w.parent
    while node is not None and node is not node.parent:
        if isinstance(node, ScrollView):
            x, y = node.to_window(node.x, node.y)
            return x, y, x + node.width, y + node.height
        node = node.parent
    return 0, 0, Window.width, Window.height


def text_box(w):
    """Window rect (x0, y0, x1, y1, y-up) that holds the drawn text."""
    x, y = w.to_window(w.x, w.y)
    if isinstance(w, TextInput):
        pl, pt, pr, pb = w.padding  # VariableListProperty: left, top, right, bottom
        return x + pl, y + pb, x + w.width - pr, y + w.height - pt
    # Label / Button: measure the text's natural extent, place it the way
    # Label places it (halign inside text_size/padding, valign middle).
    probe = Label(text=w.text, font_name=w.font_name, font_size=w.font_size,
                  bold=w.bold, markup=w.markup)
    probe.texture_update()
    tw, th = probe.texture_size
    pad = w.padding if len(w.padding) == 4 else list(w.padding) * 2
    pl, pr = pad[0], pad[2]
    avail = w.width - pl - pr
    tw = min(tw, avail)
    th = min(th, w.height)
    if w.text_size[0] is not None and w.halign in ("left", "justify"):
        x0 = x + pl
    elif w.text_size[0] is not None and w.halign == "right":
        x0 = x + w.width - pr - tw
    else:
        x0 = x + (w.width - tw) / 2
    y0 = y + (w.height - th) / 2
    return x0, y0, x0 + tw, y0 + th


def resolved_color(w):
    if isinstance(w, TextInput):
        return w.disabled_foreground_color if w.disabled else w.foreground_color
    return w.disabled_color if w.disabled else w.color


def text_widgets(screen):
    out = []
    for w in screen.walk(restrict=True):
        if isinstance(w, TextInput):
            if w.text.strip() and shown(w):
                out.append(w)
        elif isinstance(w, Label):
            if w.text.strip() and shown(w):
                out.append(w)
    return out


def measure(img, w):
    """(ratio, bg, ink) sampled from the frame inside the text's box."""
    H = img.height
    sx = img.width / Window.width
    sy = img.height / Window.height
    x0, y0, x1, y1 = text_box(w)
    vx0, vy0, vx1, vy1 = viewport(w)
    x0, y0, x1, y1 = max(x0, vx0), max(y0, vy0), min(x1, vx1), min(y1, vy1)
    px0, px1 = int(x0 * sx) + 1, int(x1 * sx) - 1
    py0, py1 = int(H - y1 * sy) + 1, int(H - y0 * sy) - 1
    if px1 <= px0 or py1 <= py0:
        return None
    region = img.crop((px0, py0, px1, py1))
    counts = region.getcolors(maxcolors=region.width * region.height + 1)
    bg = max(counts)[1]
    best, ink = 1.0, bg
    for _n, c in counts:
        r = ratio(c, bg)
        if r > best:
            best, ink = r, c
    return best, bg, ink


def label_of(w):
    t = w.text.strip().replace("\n", " ")
    kind = type(w).__name__
    return f"{kind} {t[:40]!r}"


def is_icon(w):
    return getattr(w, "font_name", "") == app.theme.font_icon


def contrast_pass(screen, tag, frame, seen, gating=True):
    """Measure every text widget fully inside its viewport in this frame."""
    img = Image.open(frame).convert("RGB")
    for w in text_widgets(screen):
        key = id(w)
        if key in seen:
            continue
        x, y = w.to_window(w.x, w.y)
        vx0, vy0, vx1, vy1 = viewport(w)
        if x < vx0 - 1 or y < vy0 - 1 or x + w.width > vx1 + 1 or y + w.height > vy1 + 1:
            continue
        m = measure(img, w)
        if m is None:
            continue
        seen[key] = w
        r, bg, ink = m
        state = " [disabled]" if w.disabled else ""
        detail = (f"{r:.2f}:1  text rgba {fmt(resolved_color(w))} -> ink {ink} "
                  f"on bg {bg}{state}")
        line = (f"{tag}: {label_of(w)} contrast >= {MIN_RATIO:.0f}:1", r >= MIN_RATIO, detail)
        # A disabled help icon is not drawn at all since 2026-09-19, so this
        # branch should stay empty; it is kept as a belt-and-braces guard in
        # case one is ever shown again. Enabled icons gate like text.
        (RESULTS if gating and not (is_icon(w) and w.disabled) else INFO).append(line)


def huge_text_widgets(screen):
    """Text widgets taller than the window (a long log file, a profiler
    dump): they can never be fully on screen, so they are measured on their
    visible part instead."""
    return [w for w in text_widgets(screen) if w.height > Window.height]


def scroll_frames(screen, tag, gating=True):
    """Shoot the screen at several scroll offsets so every row is measured
    while fully on screen. ``gating=False`` reports without failing the run."""
    svs = [w for w in screen.walk(restrict=True) if isinstance(w, ScrollView) and shown(w)]
    seen = {}
    positions = (1.0, 0.75, 0.5, 0.25, 0.0) if svs else (None,)
    for i, pos in enumerate(positions):
        for sv in svs:
            sv.scroll_y = pos
        name = f"{tag}" if i == 0 else f"{tag}_scroll{i}"
        frame = shot(name)
        contrast_pass(screen, tag, frame, seen, gating)
        if i == 0:
            img = Image.open(frame).convert("RGB")
            for w in huge_text_widgets(screen):
                m = measure(img, w)
                if m is None or id(w) in seen:
                    continue
                seen[id(w)] = w
                r, bg, ink = m
                check(f"{tag}: {label_of(w)} contrast >= {MIN_RATIO:.0f}:1 (visible part)",
                      r >= MIN_RATIO,
                      f"{r:.2f}:1  text rgba {fmt(resolved_color(w))} -> ink {ink} on bg {bg}")
    missed = [label_of(w) for w in text_widgets(screen) if id(w) not in seen]
    (RESULTS if gating else INFO).append(
        (f"{tag}: every text widget measured on screen", not missed,
         f"never fully on screen: {missed}" if missed else f"{len(seen)} measured"))


# -- per-screen state: what a lathe shows ------------------------------------
def prepare_system(screen):
    # Since 2026-09-19 the screen is one read-only row; entering it re-reads
    # free space, so pin the value AFTER goto (see run_theme).
    screen.free_space_str = "54.01 GiB"


def prepare_backup(screen):
    # Every build today has no OAuth client id: all three gist buttons are
    # disabled and the note says why. That is the state on the lathe.
    screen.gist_configured = False
    screen.gist_enabled = False
    screen.status_text = "Exported commissioning bundle to /media/usb/reflex"


def prepare_update(screen):
    screen.releases = ["v1.2.0-rc.3", "v1.1.4"]
    screen.selected_release = "v1.2.0-rc.3"
    screen.enable_update_button = False   # nothing newer selected
    screen.status = "Already on the newest release"


def prepare_network(screen):
    screen.status_text = "Connected to shop-wifi (192.168.1.40)"


def prepare_profiling(screen):
    panel = next(w for w in screen.walk(restrict=True)
                 if type(w).__name__ == "ProfilingPanel")
    panel.status_text = "Profiler stopped. Results below."
    panel.profile_results = (
        "=== Sorted by CUMULATIVE time ===\n"
        "   ncalls  tottime  percall  cumtime  percall filename:lineno(function)\n"
        "      120    0.004    0.000    0.310    0.003 dro.py:88(update)\n")


def prepare_log_viewer(screen):
    # A short, fixed log rather than this run's own: the real one is hundreds
    # of lines, which can never all be on screen for measuring, and differs
    # from run to run.
    path = os.path.join(os.environ["HOME"], "kivy_26-09-19_0.txt")
    with open(path, "w") as fh:
        fh.write("[INFO   ] [Reflex      ] v1.2.0-rc.3 starting\n"
                 "[INFO   ] [Board       ] connected, protocol v7\n"
                 "[WARNING] [Servo       ] following error 0.012 mm\n"
                 "[INFO   ] [Formats     ] theme -> light\n")
    screen.load_file(path)


PREPARE = {
    "system": prepare_system,
    "backup": prepare_backup,
    "update": prepare_update,
    "network": prepare_network,
    "profiling": prepare_profiling,
    "log_viewer": prepare_log_viewer,
}


def setup_screens():
    """The Setup menu, every sub-screen it opens, and the screens those open
    (first input, first axis, log viewer, colour/font pickers)."""
    names = ["setup_screen", "machine", "inputs_setup", "axes_setup", "servo",
             "network", "formats", "system", "logs", "profiling", "update",
             "els_setup", "backup"]
    have = app.manager.screen_names
    nested = [n for n in have if n.startswith("input_")][:1]
    nested += [n for n in have if n.startswith("axis_")][:1]
    nested += ["log_viewer", "color_picker", "font_picker"]
    out = names + nested
    return [n for n in out if not ONLY or n in ONLY]


def open_screen(name):
    if name in ("log_viewer", "color_picker", "font_picker"):
        scr = getattr(app, name)
        if not app.manager.has_screen(name):
            app.manager.add_widget(scr)
        return scr
    return app.manager.get_screen(name)


def measure_home_and_feed_picker(theme):
    """The two surfaces OUTSIDE Setup that carried the same dim-text defect.

    Added 2026-09-19 with the text_dim change: an unselected sidebar option,
    the unselected FEED/THREAD tab and the feed picker's other table all read
    2.4-2.8:1 on the machine and are fixed by the new text_dim.

    REPORTED, NOT GATED, and that is a deliberate scope line. Measuring these
    also surfaced two LIGHT-THEME defects that predate this branch and are not
    dim text:
      * every Popup is painted OVER by the modal's own 70% black overlay, so
        the picker's body renders at 30% of `background` (217 -> 66) and its
        accent_text title measures 1.14:1. Kivy's modalview.kv rule loads when
        the first Popup is built, i.e. AFTER this app's <Popup> rule, so its
        overlay instruction ends up last in canvas.before. Fixing it means
        changing popup chrome, which is not this branch's job.
      * the SELECTED sidebar option ('INC') is accent_text on accent_bg at
        2.79:1 -- a token pairing question, not a dim-text one.
    Both are Evan's call; until then these two surfaces report only.
    """
    app.manager.goto("home")
    settle(30)
    scroll_frames(app.manager.get_screen("home"), f"home_{theme}", gating=False)

    popup = Factory.FeedsTablePopup()
    popup.show_with_callback(lambda *a: None, current_mode="Feed")
    settle(30)
    scroll_frames(popup, f"feed_picker_{theme}", gating=False)
    popup.dismiss()
    settle(10)


def run_theme(theme_idx):
    theme = THEMES[theme_idx]
    try:
        app.formats.theme = theme
        settle()
        check(f"{theme}: theme applied", app.theme.name == theme, app.theme.name)
        for name in setup_screens():
            try:
                scr = open_screen(name)
            except Exception as e:  # screen not registered for this use case
                check(f"{name}_{theme}: screen available", False, repr(e))
                continue
            app.manager.goto(name)
            settle(30)
            if name in PREPARE:
                PREPARE[name](scr)
                settle(10)
            scroll_frames(scr, f"{name}_{theme}")
        measure_home_and_feed_picker(theme)
    except Exception as e:  # never let the Kivy clock swallow it
        import traceback
        traceback.print_exc()
        FAILED.append(e)
    if theme_idx + 1 < len(THEMES):
        app.manager.goto("setup_screen")
        Clock.schedule_once(lambda _dt: run_theme(theme_idx + 1), 1.0)
    else:
        report()


def report():
    if INFO:
        print("\n==== REPORTED, NOT GATED (Home, the feed picker, and any "
              "disabled help icon) ====")
        for label, ok, detail in INFO:
            print(f"{'ok  ' if ok else 'LOW '}  {label}  [{detail}]")
    print("\n==== RESULTS ====")
    for label, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {label}  [{detail}]")
    fails = sum(1 for _, ok, _ in RESULTS if not ok)
    lows = sum(1 for _, ok, _ in INFO if not ok)
    print(f"\n{len(RESULTS)} gated checks, {fails} FAIL; "
          f"{len(INFO)} reported, {lows} of them under {MIN_RATIO:.0f}:1")
    app.stop()


def arm(_dt):
    from reflex.app import MODE_ELS
    app.use_case = "lathe"
    app.set_mode(MODE_ELS)      # Home shows the ELS bar, as on the lathe
    app.manager.goto("setup_screen")
    Clock.schedule_once(lambda _dt: run_theme(0), 1.0)


Clock.schedule_once(arm, 2.0)
app.run()

if FAILED or not RESULTS or not all(ok for _, ok, _ in RESULTS):
    raise SystemExit(f"{__file__}: FAIL")
