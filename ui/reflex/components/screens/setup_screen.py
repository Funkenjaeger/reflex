"""The Setup screen: a grid of buttons, each navigating to one settings screen.

Nothing here is itself a setting or an action -- backup and restore of the
commissioning bundle moved behind the Backup button (``backup_screen``) on
2026-09-17, when having them at this level overflowed the grid on the lathe.
The ``<SetupButton>`` rule both screens use lives in
``widgets/facelift_chrome.kv`` (loaded once from ``reflex.app``).
"""
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

load_kv(__file__)


class SetupScreen(Screen):
    pass
