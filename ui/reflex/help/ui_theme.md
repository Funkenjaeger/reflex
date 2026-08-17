UI Theme
========

Selects the overall color theme for the user interface.

## Options

- **dark** — Dark steel background with cyan accents (default).
- **light** — Light brushed-aluminum background with amber accents.

Switching the theme recolors the entire UI immediately.

## Notes

- Changing the theme also resets the DRO digit color and the indicator
  on/off lamp colors to that theme's recommended values. You can still
  override those afterwards in this menu; your choices persist until the
  next time you switch themes.
- Themes are defined by simple text files, one per theme, in
  `reflex/themes/`. To add or tweak a theme, copy one of those files into
  `~/.config/reflex/themes/`, edit the colors in a text editor, and restart
  the application — the new theme appears in this list automatically.
