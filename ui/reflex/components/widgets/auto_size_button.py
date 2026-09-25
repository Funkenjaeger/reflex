from kivy.core.text import Label as CoreLabel
from kivy.properties import NumericProperty
from kivy.uix.button import Button
from kivy.uix.label import Label

from reflex.components.widgets.beep_mixin import BeepMixin


def measure_text_width(text, font_name, font_size) -> int:
    """Natural single-line width of ``text``, in pixels."""
    label = CoreLabel(text=text, font_size=font_size, font_name=font_name)
    label.refresh()
    return label.texture.size[0]


def fitted_font_size(natural_width, available, max_font_size, fill=0.95, floor=8):
    """The font size that puts text measured ``natural_width`` px wide at
    ``max_font_size`` inside ``fill`` of ``available`` px, never above
    ``max_font_size`` nor below ``floor``. Pure arithmetic, so the unit tests
    can check it: the suite has no GL context, and constructing a Kivy Label
    with text there segfaults. The widget itself is checked in the real app,
    in a window, by ui/previews/preview_version_fit.py."""
    if natural_width <= 0 or available <= 0 or natural_width <= available * fill:
        return max_font_size
    return max(floor, max_font_size * (available * fill) / natural_width)


class AutoSizeLabel(Label):
    """Label that scales font_size down so its text stays on ONE line.

    The status bar's version sits in a fixed 92 dp box with word wrap on
    (text_size = size, for centering), and a pre-release version -- plus,
    until 2026-09-25, an editable-install "*" -- was just wider than the box
    at 16 px, so it wrapped. Same fit as AutoSizeButton below: measure the
    natural width at max_font_size and scale down to 95% of the room."""
    max_font_size = NumericProperty(16)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for prop in ("text", "size", "max_font_size", "font_name"):
            self.bind(**{prop: self._fit_text})
        self._fit_text()

    def _fit_text(self, *args):
        available = self.width - self.padding[0] - self.padding[2]
        if not self.text or available <= 0:
            self.font_size = self.max_font_size
            return
        self.font_size = fitted_font_size(
            measure_text_width(self.text, self.font_name, self.max_font_size),
            available, self.max_font_size)


class AutoSizeButton(BeepMixin, Button):
    """Button that automatically scales font_size down to prevent text wrapping.

    Set max_font_size to the desired font size. The actual font_size will be
    clamped so the text fits on a single line within the available width.
    """
    max_font_size = NumericProperty(48)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(text=self._fit_text)
        self.bind(size=self._fit_text)
        self.bind(max_font_size=self._fit_text)
        self.bind(font_name=self._fit_text)

    def _fit_text(self, *args):
        if not self.text or self.width <= 0:
            self.font_size = self.max_font_size
            return

        available = self.width - self.padding[0] - self.padding[2]
        if available <= 0:
            return

        # Measure the natural (unconstrained) text width at max_font_size
        label = CoreLabel(
            text=self.text,
            font_size=self.max_font_size,
            font_name=self.font_name,
        )
        label.refresh()
        text_width = label.texture.size[0]

        if text_width > available * 0.95:
            scale = (available * 0.95) / text_width
            self.font_size = max(8, self.max_font_size * scale)
        else:
            self.font_size = self.max_font_size