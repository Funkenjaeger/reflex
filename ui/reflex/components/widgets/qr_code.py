"""A QR code drawn straight onto the canvas from segno's module matrix.

WHY. The gist sync's device flow shows a short code and the URL
``github.com/login/device``; on the shop floor the operator types that URL on
a phone. A QR code of it makes that one scan (Evan, 2026-09-17: "typing a URL
is a pain"). GitHub's device flow returns no ``verification_uri_complete``, so
the QR can carry the URL but not the code -- the eight characters are still
typed, and still shown large beside it.

HOW. ``segno`` (pure Python, no dependencies of its own) encodes; this widget
only paints: a light square for the whole symbol including the 4-module quiet
zone the spec requires, then one dark Rectangle per dark module. No image
file, no texture round-trip, no Pillow. The module size is a whole number of
pixels so every module is the same width -- a scanner reads fractional,
uneven modules much worse -- and the symbol is centred in the widget.

SEGNO IS OPTIONAL AT RUNTIME. This module is imported by backup_screen.kv,
which the screen manager loads at startup, so a hard ``import segno`` would
turn a missing wheel into a UI that does not start -- and on elspi the venv's
site-packages is root-owned, so a code deploy (git pull + restart, as the
service user) can land before anyone with sudo has run ``uv sync``. Measured
2026-09-17, before the first deploy of this widget. Without segno,
:data:`AVAILABLE` is False, nothing is drawn, and the Backup screen shows the
code and URL as text exactly as it did before the QR existed.
"""
from kivy.graphics import Color, Rectangle
from kivy.logger import Logger
from kivy.properties import ColorProperty, StringProperty
from kivy.uix.widget import Widget

try:
    import segno
except ImportError:  # pragma: no cover - exercised by test_qr_code via monkeypatch
    segno = None
    Logger.warning("qr_code: segno is not installed; the device-flow QR is disabled "
                   "(run `uv sync --frozen` in ui/)")

QUIET_ZONE = 4  # modules; the QR spec's minimum border


def available() -> bool:
    """True when a QR can be drawn (segno importable)."""
    return segno is not None


class QrCode(Widget):
    #: What the code encodes. Empty draws nothing.
    data = StringProperty("")
    dark = ColorProperty([0, 0, 0, 1])
    light = ColorProperty([1, 1, 1, 1])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(data=self._redraw, pos=self._redraw, size=self._redraw,
                  dark=self._redraw, light=self._redraw)

    def modules(self) -> list[list[bool]]:
        """The symbol INCLUDING the quiet zone, top row first; True = dark.
        Split out so the preview can check the pixels against it."""
        if not self.data or not available():
            return []
        qr = segno.make(self.data, error="m", micro=False)
        return [[bool(v) for v in row] for row in qr.matrix_iter(scale=1, border=QUIET_ZONE)]

    def geometry(self):
        """(module_px, x0, y_top, n) for the current size, or None if it
        cannot be drawn at one pixel per module or more."""
        n = len(self.modules())
        if not n:
            return None
        module = int(min(self.width, self.height) // n)
        if module < 1:
            return None
        side = module * n
        x0 = int(self.x + (self.width - side) / 2)
        y_top = int(self.y + (self.height + side) / 2)
        return module, x0, y_top, n

    def _redraw(self, *_):
        self.canvas.clear()
        geo = self.geometry()
        if geo is None:
            return
        module, x0, y_top, n = geo
        rows = self.modules()
        with self.canvas:
            Color(rgba=self.light)
            Rectangle(pos=(x0, y_top - module * n), size=(module * n, module * n))
            Color(rgba=self.dark)
            for r, row in enumerate(rows):
                y = y_top - (r + 1) * module
                c = 0
                while c < n:
                    if not row[c]:
                        c += 1
                        continue
                    start = c  # one rectangle per horizontal run of dark modules
                    while c < n and row[c]:
                        c += 1
                    Rectangle(pos=(x0 + start * module, y), size=((c - start) * module, module))
