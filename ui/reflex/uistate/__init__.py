"""UI state codes -- encode the visual UI state, replay it as a screenshot.

A short, versioned code captures everything the screen is a function of, so the
exact screen can be re-rendered later by booting the app headless and replaying
the snapshot. Logged on every relevant state change, it gives a storyboard of an
operator session for documentation or incident reconstruction, at a few hundred
bytes per interaction instead of megabytes per second of video.

See ``schema.py`` for the field registry (and the rules for changing it),
``codec.py`` for the wire format, ``digest.py`` for the drift guard, and
``recorder.py`` for the capture hooks.
"""
