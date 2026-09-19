"""Headless PNGs + text-contrast assertions for Setup > System, dark AND light.

WHY THIS EXISTS. Reported by Evan at the lathe on 2026-09-19: "most of the
entries under the main Setup > System menu are invisible, at least in dark
mode." No unit test could see it -- every widget was there, laid out, with the
right text; the text was simply drawn in a colour the eye could not find on
its background. So this check reads the PIXELS: it renders the real System
screen at the lathe's 1024x600 in each built-in theme, scrolls through the
whole list, and for every visible label, button and text field measures the
contrast of the most contrasting pixel in the text's own box against that
box's dominant (background) colour. Anything under MIN_RATIO fails.

Only the free-space figure is pinned (to what a lathe shows). Everything
else -- the kv rules, the theme, the disabled states -- is production. Since
2026-09-19 the screen is one read-only row: the upstream resize and reboot
buttons and the device readout were removed.

Icon-font glyphs (the row help "?") are measured and printed as INFO but do
not gate: they are chrome, and whether a disabled help icon should recede is a
design decision, not a legibility defect.

--sweep also renders every other Setup sub-screen in both themes and prints
their contrast results as INFO (not gating), to find other screens with the
same defect.

Run (WSL):
    cd ui && xvfb-run -a -s "-screen 0 1024x600x24" uv run \\
        python previews/preview_system_screen.py [--sweep]
"""
import os
import sys
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="reflex-system-preview-")
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
from PIL import Image  # noqa: E402

from reflex.app import MainApp  # noqa: E402

OUT_DIR = os.environ.get("OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
SCRATCH = os.path.join(OUT_DIR, "_system_scratch.png")

MIN_RATIO = 3.0          # WCAG large-text floor; every row here is >= 18 px
THEMES = ("dark", "light")
SWEEP = "--sweep" in sys.argv
SWEEP_SCREENS = ("machine", "inputs_setup", "axes_setup", "servo", "network",
                 "formats", "logs", "profiling", "update", "els_setup", "backup")

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


# ── contrast arithmetic (WCAG 2.x relative luminance) ──────────────────────
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


# ── which widgets carry text, and where is that text ───────────────────────
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


def contrast_pass(screen, tag, frame, seen, gating):
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
        # Icon glyphs (the row help "?") are chrome, not text: reported, not
        # gated -- a disabled help icon on a row with no help is meant to
        # recede, and how far is a design call this check does not make.
        is_icon = getattr(w, "font_name", "") == app.theme.font_icon
        (RESULTS if gating and not is_icon else INFO).append(line)


def scroll_frames(screen, tag, gating):
    """Shoot the screen at several scroll offsets so every row is measured
    while fully on screen; returns the frame paths."""
    svs = [w for w in screen.walk(restrict=True) if isinstance(w, ScrollView) and shown(w)]
    seen = {}
    frames = []
    positions = (1.0, 0.75, 0.5, 0.25, 0.0) if svs else (None,)
    for i, pos in enumerate(positions):
        for sv in svs:
            sv.scroll_y = pos
        name = f"{tag}" if i == 0 else f"{tag}_scroll{i}"
        frame = shot(name)
        frames.append(frame)
        contrast_pass(screen, tag, frame, seen, gating)
    missed = [label_of(w) for w in text_widgets(screen) if id(w) not in seen]
    if gating:
        check(f"{tag}: every text widget measured on screen", not missed,
              f"never fully on screen: {missed}" if missed else f"{len(seen)} measured")
    return frames


def prepare_system(screen):
    # Since 2026-09-19 the screen is one read-only row; entering it re-reads
    # free space, so pin the value AFTER goto (see run_theme).
    screen.free_space_str = "54.01 GiB"


def run_theme(theme_idx):
    theme = THEMES[theme_idx]
    try:
        app.formats.theme = theme
        settle()
        check(f"{theme}: theme applied", app.theme.name == theme, app.theme.name)
        system = app.manager.get_screen("system")
        app.manager.goto("system")
        settle(30)
        prepare_system(system)
        settle(10)
        scroll_frames(system, f"system_{theme}", gating=True)
        if SWEEP:
            for name in SWEEP_SCREENS:
                try:
                    scr = app.manager.get_screen(name)
                except Exception as e:  # screen not registered for this use case
                    INFO.append((f"sweep {name}", False, f"not available: {e}"))
                    continue
                app.manager.goto(name)
                settle(30)
                scroll_frames(scr, f"sweep_{name}_{theme}", gating=False)
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
        print("\n==== SWEEP (info, not gating) ====")
        for label, ok, detail in INFO:
            print(f"{'ok  ' if ok else 'LOW '}  {label}  [{detail}]")
    print("\n==== RESULTS ====")
    for label, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {label}  [{detail}]")
    app.stop()


def arm(_dt):
    app.use_case = "lathe"
    app.manager.goto("setup_screen")
    Clock.schedule_once(lambda _dt: run_theme(0), 1.0)


Clock.schedule_once(arm, 2.0)
app.run()

if FAILED or not RESULTS or not all(ok for _, ok, _ in RESULTS):
    raise SystemExit(f"{__file__}: FAIL")
