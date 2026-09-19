"""No stock, theme-blind Label/Button/TextInput on the Setup screens.

Found by previews/preview_setup_contrast.py on 2026-09-19: a bare kv `Label:`
draws Kivy's stock white whatever the theme -- 1.38-1.52:1 on the light page
(Formats' volume readout, the Logs heading, every Profiling stat); a stock
`Button:` draws its DISABLED label white at 30% -- 1.30:1 on light (Update's
"Install Selected Release" whenever nothing is selected); a stock TextInput
is a white well with black text in both themes (System "Operation Output").
None of them was wrong in the dark theme the screens were built in, which is
why nobody saw it.

The themed replacements live in widgets/facelift_chrome.kv: ThemedLabel,
SetupButton, FormOutputText (and the Form* row controls). This test holds the
rule on every screen reachable from Setup: a stock widget there must make a
deliberate colour choice from the theme (or the operator's format colours) --
kv: a `color`/`foreground_color`/`background_color` bound to app.theme or
app.formats in its own block; Python: a `color=`/`foreground_color=` keyword.
Whether that choice is LEGIBLE is the pixel check's job, in both themes.
"""
import ast
import os
import re

import pytest

import reflex

UI = os.path.dirname(reflex.__file__)
SCREENS = os.path.join(UI, "components", "screens")
SETUP_PANELS = os.path.join(UI, "components", "setup")

# Every screen reachable from the Setup menu (setup_screen.kv's grid), and the
# screens those open. Kept explicit so a new Setup screen is a visible edit.
SETUP_SCREENS = [
    "setup_screen", "machine_screen", "inputs_setup_screen", "input_screen",
    "axes_setup_screen", "axis_screen", "servo_screen", "network_screen",
    "formats_screen", "color_picker_screen", "font_picker_screen",
    "system_screen", "logs_screen", "log_viewer_screen", "profiling_screen",
    "update_screen", "els_setup_screen", "backup_screen",
]
STOCK = ("Label", "Button", "ToggleButton", "TextInput")
COLOUR_PROPS = ("color", "foreground_color", "background_color")
COLOUR_KWARGS = ("color", "foreground_color")


def _files(ext):
    out = [os.path.join(SCREENS, f"{n}{ext}") for n in SETUP_SCREENS]
    out = [p for p in out if os.path.exists(p)]
    out += sorted(os.path.join(SETUP_PANELS, f) for f in os.listdir(SETUP_PANELS)
                  if f.endswith(ext) and not f.startswith("__"))
    return out


def stock_kv_instances(text):
    """[(line number, class)] for bare stock-widget instances in kv `text`
    whose own property block sets no theme/format colour."""
    lines = text.splitlines()
    inst = re.compile(r"^(\s*)(%s):\s*$" % "|".join(STOCK))
    bad = []
    for i, line in enumerate(lines):
        m = inst.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        child_indent = None
        themed = False
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            ind = len(nxt) - len(nxt.lstrip())
            if ind <= indent:
                break
            if child_indent is None:
                child_indent = ind
            if ind != child_indent:
                continue          # a child widget's or canvas's own lines
            pm = re.match(r"\s*(\w+):\s*(.*)$", nxt)
            if pm and pm.group(1) in COLOUR_PROPS and re.search(
                    r"\bapp\.(theme|formats)\.", pm.group(2)):
                themed = True
        if not themed:
            bad.append((i + 1, m.group(2)))
    return bad


def stock_py_calls(source):
    """[(line number, class)] for Label(...)/Button(...)/... constructor calls
    with no colour keyword."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in STOCK
                and not any(k.arg in COLOUR_KWARGS for k in node.keywords)):
            bad.append((node.lineno, node.func.id))
    return bad


# ── the scanners themselves (a scanner that finds nothing passes vacuously) ─
def test_kv_scanner_flags_a_bare_label_and_passes_a_themed_one():
    kv = ("<X>:\n"
          "  Label:\n"
          "    text: 'white on light'\n"
          "  Label:\n"
          "    text: 'ok'\n"
          "    color: app.theme.text\n"
          "  Button:\n"
          "    text: 'child colour does not count'\n"
          "    Label:\n"
          "      color: app.theme.text\n"
          "  Button:\n"
          "    background_color: app.formats.cancel_color\n")
    assert stock_kv_instances(kv) == [(2, "Label"), (7, "Button")]


def test_py_scanner_flags_a_bare_button_and_passes_a_coloured_label():
    src = ("b = Button(text='x', disabled=True)\n"
           "l = Label(text='y', color=theme.text)\n"
           "f = Factory.SetupButton(text='z')\n")
    assert stock_py_calls(src) == [(1, "Button")]


def test_the_file_lists_are_real():
    kv, py = _files(".kv"), _files(".py")
    assert len(kv) >= 18 and len(py) >= 18, (len(kv), len(py))
    assert any(p.endswith("logs_panel.kv") for p in kv)


# ── the rule ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", _files(".kv"), ids=os.path.basename)
def test_no_theme_blind_stock_widget_in_setup_kv(path):
    with open(path, encoding="utf-8") as fh:
        bad = stock_kv_instances(fh.read())
    assert not bad, (
        f"{os.path.relpath(path, UI)}: stock {', '.join(f'{c} (line {n})' for n, c in bad)} "
        f"with no theme colour. Use ThemedLabel / SetupButton / FormOutputText "
        f"(widgets/facelift_chrome.kv) or bind its colour to app.theme.")


@pytest.mark.parametrize("path", _files(".py"), ids=os.path.basename)
def test_no_theme_blind_stock_widget_in_setup_python(path):
    with open(path, encoding="utf-8") as fh:
        bad = stock_py_calls(fh.read())
    assert not bad, (
        f"{os.path.relpath(path, UI)}: {', '.join(f'{c}() at line {n}' for n, c in bad)} "
        f"without a colour. Use Factory.SetupButton / Factory.ThemedLabel, or "
        f"pass color= from app.theme.")
