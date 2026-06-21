"""Shared color palette for the UI-facelift widgets.

Central, importable source of truth for the dark/cyan mockup theme so the
new reusable widgets (tab selector, restyled LED button, icon button,
circular Zero button, recessed displays) stay visually consistent.

These mirror the inline palette already baked into ``styled_button.kv`` /
``styled_button.py`` -- if you retune one, retune both.

Colors are plain ``[r, g, b, a]`` lists (Kivy ColorProperty-compatible).
Import the module and reference attributes, or expose it in KV via a
``#: import THEME reflex.components.widgets.facelift_theme`` directive and
reference ``THEME.ACCENT`` etc.
"""

# -- Backgrounds ------------------------------------------------------------
BACKGROUND = [0.03, 0.05, 0.07, 1]        # near-black screen background
SLATE_FILL = [0.10, 0.13, 0.17, 1]        # inactive control fill
SLATE_SHEEN = [0.22, 0.27, 0.32, 1]       # subtle inner top bevel
RECESS_FILL = [0.05, 0.07, 0.09, 1]       # sunken/inset panel fill (darker)

# -- Cyan accent ------------------------------------------------------------
ACCENT = [0.16, 0.82, 0.88, 1]            # primary cyan
ACCENT_TEXT = [0.40, 0.92, 0.98, 1]       # brighter cyan for active text
ACCENT_BG = [0.07, 0.20, 0.25, 1]         # cyan-tinted active fill
GLOW = [0.16, 0.82, 0.88, 0.22]           # soft outer glow rgba

# -- Borders / edges --------------------------------------------------------
BORDER = [0.27, 0.34, 0.41, 1]            # standard control border
BORDER_DIM = [0.16, 0.20, 0.25, 1]        # faint divider between tab segments
RECESS_EDGE_DARK = [0.0, 0.0, 0.0, 0.55]  # top/left inset shadow edge
RECESS_EDGE_LIGHT = [0.30, 0.36, 0.42, 0.35]  # bottom/right faint highlight

# -- Text -------------------------------------------------------------------
TEXT = [0.62, 0.78, 0.85, 1]              # light blue-grey body text
TEXT_DIM = [0.38, 0.46, 0.53, 1]          # dimmed / inactive text

# -- Fonts ------------------------------------------------------------------
# Mockup fonts (all SIL OFL 1.1). Chakra Petch = squared industrial UI sans;
# Share Tech Mono = telemetry mono; DSEG7 Classic = seven-segment numerals.
FONT_BOLD = "fonts/ChakraPetch-SemiBold.ttf"   # main UI labels / captions
FONT_MONO = "fonts/ShareTechMono-Regular.ttf"  # status-bar telemetry + version
FONT_SEG = "fonts/DSEG7Classic-Regular.ttf"    # DRO / RPM / Stop Z / feed numerals
FONT_ICON = "fonts/Font Awesome 6 Free-Solid-900.otf"
