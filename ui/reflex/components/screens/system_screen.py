"""Setup > System: free space on the card, and nothing else.

Read-only by design. The operator UI does no system administration (Evan,
2026-09-19), so the upstream "Resize Partition" and "Reboot System" buttons
are gone -- both also failed silently once the UI stopped running as root --
and so is the root-device / disk / partition readout. Free space stays: the
flight recorder and the commissioning ledger write to this card.
"""
import shutil

from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv
from reflex.utils.platform import format_bytes

log = Logger.getChild(__name__)
load_kv(__file__)


class SystemScreen(Screen):
    free_space_str = StringProperty("N/A")

    def on_pre_enter(self, *args):
        """Re-read on every visit: free space moves, and a figure read once at
        app start would be stale by the time anyone looked."""
        self.refresh()

    def refresh(self, path: str = "/"):
        try:
            self.free_space_str = format_bytes(shutil.disk_usage(path).free)
        except OSError as e:
            log.warning(f"system screen: cannot read free space on {path} ({e})")
            self.free_space_str = "N/A"
