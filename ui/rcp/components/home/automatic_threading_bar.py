import os

from kivy.lang import Builder
from kivy.logger import Logger
from kivy.uix.boxlayout import BoxLayout
from kivy.properties import NumericProperty, BooleanProperty, StringProperty

from rcp.components.home.automatic_threading_settings_popup import AutomaticThreadingSettingsPopup
from rcp.dispatchers import SavingDispatcher

log = Logger.getChild(__name__)

kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading KV file: {kv_file}")
    Builder.load_file(kv_file)


class AutomaticThreadingBar(BoxLayout, SavingDispatcher):    
    selected_cross_slide_scale_id = NumericProperty(0)
    selected_saddle_scale_id = NumericProperty(1)
    cross_slide_diameter_mode = BooleanProperty(True)
    
    reversing_speed = NumericProperty(500)
    metric_distances = BooleanProperty(True) # This is for the UI in the setting screen
    backlash_retraction_distance = NumericProperty(10)
    backlash_cusion = NumericProperty(2)
    
    metric_mode = BooleanProperty(True) # This is for the actual threading logic
    selected_pitch = StringProperty("")
    thread_profile_angle = NumericProperty(60)
    shaft_diameter = NumericProperty(1)
    
    is_running = BooleanProperty(False)
    _skip_save = ["is_running"]

    def __init__(self, **kv):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
    
    def toggle_is_running(self):
        self.is_running = not self.is_running
        self.app.servo.toggle_enable()
    
    def open_settings(self):
        popup = AutomaticThreadingSettingsPopup(automaticThreadingBar=self)
        popup.open()