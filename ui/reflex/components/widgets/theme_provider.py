"""Reactive UI theme provider.

A single ``ThemeProvider`` (exposed as ``app.theme``) holds one observable
property per *semantic* color/font token. KV binds to ``app.theme.<token>``
(e.g. ``app.theme.accent``) so that swapping the active palette at runtime
re-colors the entire UI live -- with NO per-widget conditional logic.

The contract is intentionally semantic, not literal: ``accent`` is "the primary
highlight color", which is cyan in the dark theme and amber in the light
(brushed-aluminum) theme. Widgets ask for the *role* and the active palette
decides the value. The only place that "selects" a palette is :meth:`apply`,
driven by data (a palette dict) -- never an ``if dark else light`` scattered in
KV or widget code.

Adding a theme = add a palette dict to ``palettes.py``. Adding a token = add a
``ColorProperty``/``StringProperty`` here and a value to every palette.
"""

from kivy.event import EventDispatcher
from kivy.logger import Logger
from kivy.properties import ColorProperty, StringProperty, OptionProperty

from reflex.components.widgets import palettes

log = Logger.getChild(__name__)


class ThemeProvider(EventDispatcher):
    DEFAULT = palettes.DEFAULT

    # Active palette name. Setting it re-applies the whole palette.
    name = OptionProperty(palettes.DEFAULT, options=list(palettes.PALETTES))

    @staticmethod
    def available_themes() -> list[str]:
        return list(palettes.PALETTES)

    # ── Surfaces ───────────────────────────────────────────────────────────
    background = ColorProperty()       # screen background
    surface = ColorProperty()          # raised control fill
    surface_sheen = ColorProperty()    # subtle top bevel on a surface
    recess = ColorProperty()           # sunken / inset panel fill

    # ── Accent (primary highlight: cyan in dark, amber in light) ───────────
    accent = ColorProperty()           # primary accent line/border/indicator
    accent_text = ColorProperty()      # high-emphasis accent-colored text
    accent_bg = ColorProperty()        # accent-tinted active fill
    glow = ColorProperty()             # soft accent glow (low-alpha)

    # ── Borders / edges ────────────────────────────────────────────────────
    border = ColorProperty()           # standard control border
    border_dim = ColorProperty()       # faint divider
    edge_dark = ColorProperty()        # inset shadow edge (top/left)
    edge_light = ColorProperty()       # inset highlight edge (bottom/right)

    # ── Text ───────────────────────────────────────────────────────────────
    text = ColorProperty()             # body text
    text_dim = ColorProperty()         # secondary / inactive text
    text_disabled = ColorProperty()    # disabled text

    # ── Status ─────────────────────────────────────────────────────────────
    success = ColorProperty()          # armed / ok indicator
    success_text = ColorProperty()     # ok status caption
    success_bg = ColorProperty()       # ok-tinted fill
    danger = ColorProperty()           # stop / error indicator
    danger_text = ColorProperty()      # error status caption
    danger_bg = ColorProperty()        # error-tinted fill
    danger_glow = ColorProperty()      # soft danger glow (low-alpha)
    warning = ColorProperty()          # caution / out-of-range
    led_off = ColorProperty()          # inactive indicator lamp

    # ── Plot / scene ───────────────────────────────────────────────────────
    plot_bg = ColorProperty()          # plot canvas background
    plot_grid = ColorProperty()        # plot grid lines
    plot_tool = ColorProperty()        # tool marker

    # ── Assets ─────────────────────────────────────────────────────────────
    logo = StringProperty()            # Reflex wordmark image for this theme

    # ── Fonts (theme-invariant for now, exposed uniformly) ─────────────────
    font_bold = StringProperty()
    font_mono = StringProperty()
    font_seg = StringProperty()
    font_icon = StringProperty()

    def __init__(self, name: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._assert_palettes_complete()
        if name is not None and name in palettes.PALETTES:
            self.name = name
        self.apply(palettes.PALETTES[self.name])

    def _assert_palettes_complete(self):
        """Fail loud at startup if a palette is missing a declared token (which
        would otherwise silently render as default white)."""
        tokens = {
            n for n, p in self.properties().items()
            if isinstance(p, (ColorProperty, StringProperty)) and n != "name"
        }
        for pal_name, pal in palettes.PALETTES.items():
            missing = tokens - set(pal)
            if missing:
                raise ValueError(
                    f"Palette '{pal_name}' is missing tokens: {sorted(missing)}"
                )

    def on_name(self, _instance, value):
        palette = palettes.PALETTES.get(value)
        if palette is None:
            log.warning(f"Unknown theme '{value}', keeping current")
            return
        log.info(f"Applying UI theme '{value}'")
        self.apply(palette)

    def apply(self, palette: dict):
        """Push every token value from ``palette`` onto our properties.

        Data-driven: the palette dict is the single source of truth, so there
        is exactly one place that maps a theme to concrete colors.
        """
        for token, value in palette.items():
            setattr(self, token, value)
