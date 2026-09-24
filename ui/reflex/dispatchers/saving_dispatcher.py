import os
import shutil
from typing import Optional

import yaml
from kivy.logger import Logger
from kivy.event import EventDispatcher
from kivy.properties import StringProperty, NumericProperty, BooleanProperty, ObservableList, partial

from reflex.utils import commissioning_ledger, commissioning_state
from reflex.utils.paths import config_dir

log = Logger.getChild(__name__)


class SavingDispatcher(EventDispatcher):
    _skip_save = []
    _force_save = []
    _save_class_name: str | None = None
    id_override = StringProperty("")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Check if we have an id_override
        if self.id_override == "":
            self.id_override = f"{self.uid}"

        # Read the settings from file and into a dictionary
        self.read_settings()
        self.bind_settings()

    def get_our_properties(self):
        our_elements = dir(type(self))
        properties = [getattr(type(self), item) for item in our_elements]
        properties = [
            item
            for item in properties
            if type(item) in [NumericProperty, StringProperty, BooleanProperty]
        ]
        properties = [item for item in properties if item.name not in self._skip_save]

        force_properties = [getattr(type(self), item) for item in self._force_save]
        properties.extend(force_properties)
        return properties

    @property
    def filename(self):
        settings_folder = config_dir()
        os.makedirs(settings_folder, exist_ok=True)

        class_name = self._save_class_name or self.__class__.__name__
        settings_path = settings_folder / f"{class_name}-{self.id_override}.yaml"
        return settings_path

    def read_settings(self):
        props = self.get_our_properties()
        prop_names = [item.name for item in props]

        config_data = read_settings(self.filename)
        if config_data is None:
            self.save_settings()
            return

        for k, v in config_data.items():
            if k == "id_override":
                # NEVER load this one. It is the key in `filename`, so honouring
                # a stored value lets a file rename itself back: copy
                # Foo-2164.yaml to Foo-0.yaml and the `id_override: '2164'`
                # inside would point the next save at Foo-2164.yaml again. It is
                # written to the file for legibility; it is not an input.
                continue
            if k in prop_names:
                self.__setattr__(k, v)
            else:
                log.debug(f"Provided property with name: {k} is unknown to this class")

    def bind_settings(self):
        props = self.get_our_properties()
        prop_names = [item.name for item in props]
        kwargs = {item: partial(self.save_settings, triggering_property=item) for item in prop_names}
        self.bind(**kwargs)

    def _get_extra_save_data(self) -> dict:
        """Override in subclasses to include additional data in save files."""
        return {}

    def save_settings(self, *args, **kv):
        triggering_property = kv.pop("triggering_property", "")
        props = self.get_our_properties()
        prop_names = [item.name for item in props]
        data = dict()
        for item in prop_names:
            data[item] = self.__getattribute__(item)
            if isinstance(data[item], ObservableList):
                data[item] = list(data[item])

        data.update(self._get_extra_save_data())
        write_settings(self.filename, data, triggered_by=triggering_property)


def read_settings(file: str):
    if not os.path.exists(file):
        return None

    try:
        with open(file, "r") as f:
            data = yaml.safe_load(f.read())
            return data
    except (OSError, yaml.YAMLError) as e:
        log.error(str(e))
        return None


def write_settings(file: str, data, triggered_by: Optional[str] = ""):
    # ── THE UNCOMMISSIONED GATE ──────────────────────────────────────────────
    # One check, in the one function that both writes the file and records the
    # commissioning event, rather than a copy per dispatcher. On a machine that
    # does not meet the restore contract (reflex.utils.commissioning_state,
    # latched once by MainApp.build before any dispatcher exists) there is
    # nothing here worth persisting: every value in `data` is an in-code
    # default, not a measurement of this lathe.
    #
    # BOTH HALVES OF THE REFUSAL MATTER, and it is the file half that is easy
    # to miss. Skipping only the ledger record would still leave ~19 default
    # YAML files on the card -- which is itself past the restore contract's
    # 15-file bar, so the NEXT boot would latch "commissioned" on invented
    # geometry and the uncommissioned state would last exactly one session.
    # Refusing the write is what keeps the card honestly empty until a human
    # has restored a real capture onto it.
    #
    # Returning False (the same value an OSError returns) is honest: nothing
    # was written. No caller branches on it today; the log line and the
    # on-screen banner are how anyone finds out.
    if not commissioning_state.latched():
        log.warning(
            f"Refusing to save {triggered_by or 'settings'} to {file}: this "
            f"machine is UNCOMMISSIONED. These are in-code defaults, not "
            f"measured values, and writing them would record them as the "
            f"commissioning baseline."
        )
        return False

    log.info(f"Saving {triggered_by}: {file}")
    # Read the outgoing state BEFORE overwriting it -- this is the last moment
    # it exists. `None` when the file is new, which the ledger treats as the
    # commissioning event for that dispatcher.
    previous = read_settings(file)
    try:
        with open(file, "w") as f:
            yaml.dump(data, f)

    except (OSError, yaml.YAMLError) as e:
        log.error(str(e))
        return False

    # After a SUCCESSFUL write only, and OUTSIDE the try so it cannot turn a
    # written file into a `False` return. record() swallows its own failures.
    commissioning_ledger.record(file, previous, data, triggered_by or "")
    return True
