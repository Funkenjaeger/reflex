import os

from kivy.lang import Builder
from kivy.logger import Logger
from kivy.uix.popup import Popup
from kivy.properties import ObjectProperty

from rcp import feeds

log = Logger.getChild(__name__)

kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading KV file: {kv_file}")
    Builder.load_file(kv_file)


class AssistedThreadingSettingsPopup(Popup):
    assistedThreadingBar = ObjectProperty(None)
    
    def __init__(self, **kv):
        super().__init__(**kv)
        
    def get_pitches(self):
        if not self.assistedThreadingBar:
            return []

        # Choose the correct table based on metric_mode
        if self.assistedThreadingBar.metric_mode:
            return [f.name for f in feeds.table["Thread MM"]]
        else:
            return [f.name for f in feeds.table["Thread IN"]]
    
    def set_thread_profile_angle(self, value):
        try:
            angle = float(value) 
        except (ValueError, TypeError):
            angle = 1
            
        angle = abs(angle)
        if angle <= 0 or angle > 90:
            angle = 90
            
        self.assistedThreadingBar.thread_profile_angle = angle
        
    def set_shaft_diameter(self, value):
        try:
            diameter = float(value) 
        except (ValueError, TypeError):
            diameter = 1
        self.assistedThreadingBar.shaft_diameter = abs(diameter)
        
        
    def on_metric_mode_changed(self, value):
        self.assistedThreadingBar.metric_mode = value
        pitches_dropdown = self.ids.pitches_dropdown
        pitches_dropdown.value = ""
        pitches_dropdown.options = self.get_pitches()
        log.info(f"Metric mode changed to: {value}")
        
    def on_pitch_selected(self, selected_pitch):
        self.assistedThreadingBar.selected_pitch = selected_pitch
        log.info(f"Selected pitch: {selected_pitch}")