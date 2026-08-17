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
from kivy.properties import (
    ColorProperty, StringProperty, OptionProperty, ObjectProperty,
)

from reflex.components.widgets import palettes
from reflex.components.widgets.gradients import (
    vgrad_texture, top_shadow_texture, side_shadow_texture, radial_glow_texture,
)

log = Logger.getChild(__name__)


def _lerp(rgba, target: float, t: float):
    """Blend an rgba toward a grey ``target`` (0=black, 1=white) by ``t``,
    preserving alpha. Used to derive gradient stops from a single base color so
    every theme -- including user-authored ones -- gets depth for free."""
    return [rgba[i] + (target - rgba[i]) * t for i in range(3)] + [rgba[3]]


class ThemeProvider(EventDispatcher):
    DEFAULT = palettes.DEFAULT

    # Active palette name. Setting it re-applies the whole palette.
    name = OptionProperty(palettes.DEFAULT, options=list(palettes.PALETTES))

    @staticmethod
    def available_themes() -> list[str]:
        return list(palettes.PALETTES)

    # ── Surfaces ───────────────────────────────────────────────────────────
    background = ColorProperty()       # screen background
    surface = ColorProperty()          # raised control fill (gradient top)
    surface_sheen = ColorProperty()    # 1px top inner highlight line (low-alpha)
    recess = ColorProperty()           # sunken / inset panel fill (FLAT)
    tab_bg = ColorProperty()           # mode-tab face (FLAT, darker than surface)

    # ── Accent (primary highlight: cyan in dark, amber in light) ───────────
    accent = ColorProperty()           # primary accent line/border/indicator
    accent_text = ColorProperty()      # high-emphasis accent-colored text
    accent_bg = ColorProperty()        # accent-tinted active fill (opaque)
    accent_soft = ColorProperty()      # translucent accent tint (active seg/Cut)
    glow = ColorProperty()             # soft accent glow (low-alpha)

    # ── Borders / edges ────────────────────────────────────────────────────
    border = ColorProperty()           # standard control border
    border_dim = ColorProperty()       # faint divider
    edge_dark = ColorProperty()        # recessed top inner-shadow color
    edge_light = ColorProperty()       # recessed bottom inner-highlight color
    shadow = ColorProperty()           # raised drop-shadow (mostly light theme)
    edge_shadow = ColorProperty()      # strong directional shadow (tab spine)

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

    # ── Readout ────────────────────────────────────────────────────────────
    readout_ghost = ColorProperty()    # off-segment "ghost" behind 7-seg digits

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

    # ── Derived depth textures (rebuilt at the end of apply()) ─────────────
    # Cheap gradient/shadow textures derived from the base color tokens so
    # raised faces read domed and wells read sunken without box-shadow. KV
    # paints these with a white Color so the texture shows its own colors;
    # the active/pressed state swaps the texture for a flat accent fill.
    surface_texture = ObjectProperty(None, allownone=True)     # raised button face
    tab_texture = ObjectProperty(None, allownone=True)         # mode-tab face
    background_texture = ObjectProperty(None, allownone=True)  # app background
    recess_shadow = ObjectProperty(None, allownone=True)       # well top inner-shadow
    spine_shadow = ObjectProperty(None, allownone=True)        # tab-spine right edge
    glow_texture = ObjectProperty(None, allownone=True)        # soft LED halo (white)

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
        # Rebuild derived textures from the *final* token values -- after the
        # whole loop, so we never read a half-applied palette (the same stale-
        # mid-sweep hazard the dropdown rebuild guards against).
        self._rebuild_textures()

    def _rebuild_textures(self):
        # Raised face: a GENTLE 2-stop vertical wash -- top == surface, base a
        # touch darker. Small delta, single direction (the texture is painted
        # on a stencil-clipped flat Rectangle in KV so it stays planar; a
        # RoundedRectangle would map it radially and pillow). No over-bright top.
        self.surface_texture = vgrad_texture(
            _lerp(self.surface, 0.0, 0.14),   # base: slightly darker
            self.surface,                     # top: equals surface
        )
        # Tab face: same gentle dome, derived from the (darker) tab_bg so tabs
        # read as their own surface against the recessed spine.
        self.tab_texture = vgrad_texture(
            _lerp(self.tab_bg, 0.0, 0.10),
            _lerp(self.tab_bg, 1.0, 0.06),
        )
        # App background: a barely-there top-down lift, not a feature.
        self.background_texture = vgrad_texture(
            _lerp(self.background, 0.0, 0.05),
            self.background,
        )
        # Recessed wells: a short dark band tucked under the top lip only.
        self.recess_shadow = top_shadow_texture(self.edge_dark)
        # Tab spine: a narrow shadow ramping to dark at the column's right edge.
        self.spine_shadow = side_shadow_texture(self.edge_shadow)
        # Soft LED halo (theme-invariant white; tinted per-LED at draw time).
        self.glow_texture = radial_glow_texture()
