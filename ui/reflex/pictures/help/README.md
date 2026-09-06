# Help topic illustrations

One optional PNG per help topic, named after the topic's help file in
`reflex/help/`, with the `.md` swapped for `.png`:

| Help topic                        | Illustration                             |
|-----------------------------------|------------------------------------------|
| `help/els_thread_resync.md`       | `pictures/help/els_thread_resync.png`    |
| `help/els_phase_offset.md`        | `pictures/help/els_phase_offset.png`     |

Drop the file in and that topic's help popup grows a picture above its prose —
there is no code, kv or registry to update (`components/popups/help_popup.py`
resolves the name). A topic with no file here simply has no picture, so an
undrawn illustration is never a broken popup.

PNG, not SVG: Kivy has no SVG loader in this app's image pipeline.

Sizing: the popup scrolls, and draws the image `fit_mode: "contain"` into a
220 dp band the full width of the popup — roughly 800x220 on the machine's
1024x600 screen. A wide, short drawing fills that band; a tall one is letter-
boxed rather than cropped. Transparent background so it works on both themes,
and no baked-in text that only reads on one of them (see the `_dark`/`_light`
variants in the parent directory for the defect that caused).
