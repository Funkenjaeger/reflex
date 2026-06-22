"""Theme palettes — the single source of truth mapping each theme to concrete
colors. Consumed by :class:`reflex.components.widgets.theme_provider.ThemeProvider`.

Two visually distinct, color-only themes:

- ``dark``  — the dark-steel / cyan "facelift" look (current default).
- ``light`` — a light brushed-aluminum look with amber/yellow highlights.

Every palette MUST define every token declared on ``ThemeProvider`` (colors as
``[r, g, b, a]``; fonts as paths). Keep the two palettes structurally identical;
only values differ.
"""

# Fonts are theme-invariant (the brief is color-only) but are exposed through
# the provider so KV references a single namespace (``app.theme.font_bold``).
FONTS = {
    "font_bold": "fonts/ChakraPetch-SemiBold.ttf",
    "font_mono": "fonts/ShareTechMono-Regular.ttf",
    "font_seg": "fonts/DSEG7Classic-Regular.ttf",
    "font_icon": "fonts/Font Awesome 6 Free-Solid-900.otf",
}

# ── Dark steel / cyan ──────────────────────────────────────────────────────
DARK = {
    # surfaces
    "background": [0.03, 0.05, 0.07, 1],
    "surface": [0.10, 0.13, 0.17, 1],
    "surface_sheen": [0.22, 0.27, 0.32, 1],
    "recess": [0.05, 0.07, 0.09, 1],
    "readout_bg": [0.0, 0.0, 0.0, 1],
    "readout": [0.251, 0.878, 0.929, 1],   # cyan, matches display_color default
    # accent (cyan)
    "accent": [0.16, 0.82, 0.88, 1],
    "accent_text": [0.40, 0.92, 0.98, 1],
    "accent_bg": [0.07, 0.20, 0.25, 1],
    "glow": [0.16, 0.82, 0.88, 0.22],
    # borders / edges
    "border": [0.27, 0.34, 0.41, 1],
    "border_dim": [0.16, 0.20, 0.25, 1],
    "edge_dark": [0.0, 0.0, 0.0, 0.55],
    "edge_light": [0.30, 0.36, 0.42, 0.35],
    # text
    "text": [0.62, 0.78, 0.85, 1],
    "text_dim": [0.38, 0.46, 0.53, 1],
    "text_disabled": [0.40, 0.44, 0.48, 1],
    # status
    "success": [0.20, 0.78, 0.35, 1],
    "success_text": [0.45, 0.88, 0.55, 1],
    "success_bg": [0.10, 0.20, 0.12, 1],
    "danger": [0.85, 0.20, 0.20, 1],
    "danger_text": [1.0, 0.62, 0.62, 1],
    "danger_bg": [0.28, 0.07, 0.07, 1],
    "danger_glow": [0.95, 0.25, 0.25, 0.22],
    "warning": [1.0, 0.80, 0.21, 1],
    "led_off": [0.45, 0.45, 0.45, 1],
    # plot / scene
    "plot_bg": [0.102, 0.102, 0.180, 1],
    "plot_grid": [0.165, 0.165, 0.243, 1],
    "plot_tool": [1.0, 0.42, 0.42, 1],
    **FONTS,
}

# ── Light brushed aluminum / amber ─────────────────────────────────────────
LIGHT = {
    # surfaces: cool light greys, surfaces slightly brighter than the base so
    # raised controls read; recess slightly darker.
    "background": [0.77, 0.79, 0.81, 1],
    "surface": [0.86, 0.88, 0.90, 1],
    "surface_sheen": [0.96, 0.97, 0.98, 1],
    "recess": [0.70, 0.72, 0.74, 1],
    "readout_bg": [0.10, 0.11, 0.12, 1],   # dark instrument window in the panel
    "readout": [0.20, 0.22, 0.25, 1],      # charcoal numerals on light panels
    # accent (amber): deep amber reads as a line/border/indicator on light grey
    # (a lighter amber failed contrast as a separator/axis line).
    "accent": [0.68, 0.37, 0.0, 1],
    "accent_text": [0.42, 0.26, 0.0, 1],   # deep amber/brown text
    "accent_bg": [0.99, 0.88, 0.66, 1],    # light amber active fill
    "glow": [0.95, 0.62, 0.11, 0.30],
    # borders / edges
    "border": [0.42, 0.45, 0.48, 1],
    "border_dim": [0.55, 0.58, 0.61, 1],
    "edge_dark": [0.0, 0.0, 0.0, 0.22],
    "edge_light": [1.0, 1.0, 1.0, 0.55],
    # text: charcoal on aluminum
    "text": [0.13, 0.15, 0.17, 1],
    "text_dim": [0.30, 0.33, 0.36, 1],
    "text_disabled": [0.50, 0.52, 0.54, 1],
    # status (tuned for light backgrounds)
    "success": [0.08, 0.48, 0.18, 1],
    "success_text": [0.06, 0.34, 0.13, 1],
    "success_bg": [0.78, 0.90, 0.78, 1],
    "danger": [0.74, 0.10, 0.10, 1],
    "danger_text": [0.50, 0.04, 0.04, 1],
    "danger_bg": [0.96, 0.80, 0.78, 1],
    "danger_glow": [0.80, 0.12, 0.12, 0.26],
    "warning": [0.64, 0.33, 0.0, 1],
    "led_off": [0.58, 0.60, 0.62, 1],
    # plot / scene
    "plot_bg": [0.82, 0.84, 0.86, 1],
    "plot_grid": [0.66, 0.68, 0.70, 1],
    "plot_tool": [0.80, 0.18, 0.18, 1],
    **FONTS,
}

PALETTES = {
    "dark": DARK,
    "light": LIGHT,
}

DEFAULT = "dark"
