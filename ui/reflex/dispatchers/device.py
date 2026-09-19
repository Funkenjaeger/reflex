"""The machine's use case and selected operating mode, persisted as ``Device-0.yaml``.

WHY A STEM. Until this module existed, ``use_case`` and ``current_mode`` were
the last two settings still stored in the checkout's ``ui/config.ini``
(``[device]`` section, Kivy ``ConfigParserProperty``s on ``MainApp``) -- a
leftover of the 2026-06-19 fork. Everything else had already moved to YAML
stems under :func:`reflex.utils.paths.config_dir`, which is what the
commissioning ledger, the snapshots and the USB export capture. A card that
lost ``config.ini`` came back as a rotary table, with no ELS mode, and a
bundle restore could not put the lathe back. Here the two keys are one more
``SavingDispatcher`` stem, so they travel with everything else.

THE NAME. ``SavingDispatcher`` names a file ``<_save_class_name>-<id_override>``,
and the short-name convention (``Axis``, ``Els``, ``CoordBar``) drops the
``Dispatcher`` suffix. There is one device per app, so the instance id is
``"0"``, as for ``Els-0`` and ``FormatsDispatcher-0``: ``Device-0.yaml``. The
bundle refers to the same name as :data:`DEVICE_STEM`.

ONE-TIME MIGRATION FROM config.ini. See :meth:`DeviceDispatcher.read_settings`.
The rule: if ``Device-0.yaml`` does not exist and the legacy ini has a
``[device]`` section, its ``use_case`` / ``current_mode`` seed the new stem,
which the ordinary missing-file path then writes. Once the stem exists, the
ini is never read for these keys again. The ini is left exactly as it was.
"""
import configparser
import os

from kivy.logger import Logger
from kivy.properties import NumericProperty, StringProperty

from reflex.dispatchers.saving_dispatcher import SavingDispatcher

log = Logger.getChild(__name__)

#: Use case of a machine that has never been told otherwise.
DEFAULT_USE_CASE = "rotary_table"

#: Operating mode of a machine that has never been told otherwise (MODE_INDEX).
DEFAULT_MODE = 1

#: The stem this dispatcher writes, as the commissioning bundle names it.
DEVICE_STEM = "Device-0"

#: The ini section and keys the legacy ``ConfigParserProperty``s used.
LEGACY_SECTION = "device"
LEGACY_KEYS = ("use_case", "current_mode")


def legacy_ini_path() -> str | None:
    """The legacy ``ui/config.ini``, or ``None`` if it cannot be located.

    Resolved through ``reflex.components.appsettings.config_path``, the same
    constant the app used when these keys lived there. A seam so a test can
    aim it at a tmp file: the real file is gitignored.
    """
    try:
        from reflex.components.appsettings import config_path
        return config_path
    except Exception as e:  # pragma: no cover - only if the app package breaks
        log.error(f"device settings: cannot locate legacy config.ini ({e})")
        return None


def read_legacy_device_section(path) -> dict | None:
    """``{key: str}`` for the legacy keys present in ``path``'s ``[device]``
    section, or ``None`` when the file or section is absent.

    Parsed with the stdlib, not Kivy's ``ConfigParser``, which writes defaults
    back into the file it reads: this path must leave the ini untouched.
    """
    if path is None or not os.path.exists(path):
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except (OSError, configparser.Error) as e:
        log.error(f"device settings: cannot parse legacy {path} ({e})")
        return None
    if not parser.has_section(LEGACY_SECTION):
        return None
    section = parser[LEGACY_SECTION]
    return {k: section[k] for k in LEGACY_KEYS if k in section}


class DeviceDispatcher(SavingDispatcher):
    """``use_case`` (commissioning tier) and ``current_mode`` (operational
    tier; see ``reflex/utils/commissioning_scope.py``).

    ``MainApp`` keeps its own ``use_case`` / ``current_mode`` properties for kv
    bindings and mirrors them into this object, which only persists them.
    """
    _save_class_name = "Device"

    use_case = StringProperty(DEFAULT_USE_CASE)
    current_mode = NumericProperty(DEFAULT_MODE)

    def read_settings(self):
        # Before the base class looks at the file: when the stem is missing it
        # writes the CURRENT property values, so seeding them here is all the
        # migration needs to produce a stem that says what the ini said.
        if not os.path.exists(self.filename):
            self._migrate_from_legacy_ini()
        super().read_settings()

    def _migrate_from_legacy_ini(self):
        # The legacy ui/config.ini is NOT deleted or rewritten. After this runs
        # once (the stem now exists), its [device] use_case / current_mode are
        # ignored; any other keys in it are still read by their own owners.
        path = legacy_ini_path()
        legacy = read_legacy_device_section(path)
        if not legacy:
            return
        taken = {}
        if legacy.get("use_case"):
            self.use_case = legacy["use_case"]
            taken["use_case"] = self.use_case
        if "current_mode" in legacy:
            try:
                self.current_mode = int(legacy["current_mode"])
                taken["current_mode"] = self.current_mode
            except ValueError:
                log.warning(f"device settings: ignoring unparseable legacy "
                            f"current_mode {legacy['current_mode']!r} in {path}")
        if taken:
            log.info(f"device settings: migrated {taken} from {path} "
                     f"to {self.filename} (the ini is no longer read for these)")
