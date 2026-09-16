"""The minimum elspi image release this reflex release needs to run.

Read the same way ``ui/reflex/utils/els_stop_map.py``'s ``PROTOCOL_VERSION``
is read: a bare integer literal, meant to be checked out at a TAG and
text-scanned by ``reflex.utils.updater`` rather than imported -- importing an
unreviewed tag's code to answer one number is a bigger risk than the question
is worth, and a generated-or-hand-written bare literal is stable by
construction for a scan in a way an expression or an alias is not.

KEPT IN ITS OWN TINY MODULE rather than beside ``ELS_PROTOCOL_VERSION`` in
``devices.py``, for two reasons: order 2026-09-14#6 is not permitted to touch
``devices.py`` (bound to ``updater.py``, one new small module, the update
dialog, and its own test file), and ``devices.py`` already pulls in
``els_stop_map`` (a generated file) and sits in the Kivy-adjacent import
graph. This module has zero imports, so release tooling outside ``ui/`` --
the release notes generator, packaging scripts -- can read the one
bare-literal declaration without dragging in Kivy or the register map. (No
release-notes generator exists in this repo yet; when one is added it should
read this file the same way.)

Bump this by hand when a release starts depending on something only a newer
elspi image provides (a newer kivy build, an apt package, a changed
provisioning primitive) -- the UI-side counterpart to IMAGE_RELEASE's own
bump rule in elspi's docs/provisioning.md (order 2026-09-14#5).
"""

# Bump to 1 once an image carrying /etc/elspi-release (elspi order 2026-09-14#5) is on the machine; decided by Evan 2026-09-16.
MINIMUM_IMAGE_RELEASE = 0
