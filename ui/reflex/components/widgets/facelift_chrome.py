"""Global facelift chrome: restyles Kivy's stock Popup and defines the shared
themed form controls used to build the settings menus.

Importing this module once (done from ``reflex.app``) loads ``facelift_chrome.kv``,
which installs a ``<Popup>`` style rule (dark-steel frame, cyan Chakra title) and
a set of dynamic classes (``FormHelpButton``, ``FormLabel``, ``FormValueButton``,
``FormToggle``, ``FormTextInput``) that the per-row settings widgets reference so
every modal menu shares one consistent look.
"""

import os

from kivy.logger import Logger
from kivy.lang import Builder

log = Logger.getChild(__name__)

kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading facelift chrome KV {kv_file}")
    Builder.load_file(kv_file)
