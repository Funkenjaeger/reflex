from kivy.factory import Factory
from kivy.logger import Logger
from kivy.properties import StringProperty, ObjectProperty, NumericProperty, BooleanProperty, AliasProperty
from kivy.uix.boxlayout import BoxLayout

from rcp import feeds
from rcp.dispatchers.saving_dispatcher import SavingDispatcher
from rcp.utils.kv_loader import load_kv


log = Logger.getChild(__name__)
load_kv(__file__)

class ElsAdvancedBar(BoxLayout, SavingDispatcher):
    stop_position = NumericProperty(0)
  
    _skip_save = [
        "stop_position",
        "position",
        "x", "y",
        "minimum_width",
        "minimum_height",
        "width", "height",
    ]

    def __init__(self, **kwargs):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self.wizard_callback = None
        super().__init__(**kwargs)

    def on_wizard_pressed(self):
        if self.wizard_callback:
            self.wizard_callback()