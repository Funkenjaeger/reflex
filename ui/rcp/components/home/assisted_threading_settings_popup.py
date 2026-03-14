import os

from kivy.lang import Builder
from kivy.logger import Logger
from kivy.uix.popup import Popup
from kivy.properties import ObjectProperty

from rcp import feeds
from rcp.components.home.coordbar import CoordBar
from rcp.components.home.thread_type import ThreadType

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
            self.current_feeds_table = feeds.table["Thread MM"]
        else:
            self.current_feeds_table = feeds.table["Thread IN"]
        return [f.name for f in self.current_feeds_table]
    
    def get_thread_types(self):
        """Get available thread types based on metric mode."""
        if self.assistedThreadingBar.metric_mode:
            return [ThreadType.ISO_METRIC.value, ThreadType.ACME.value]
        else:
            return [ThreadType.UNIFIED.value, ThreadType.WHITWORTH.value, ThreadType.ACME.value]
    
    def on_metric_mode_changed(self, value):
        self.assistedThreadingBar.metric_mode = value
        pitches_dropdown = self.ids.pitches_dropdown
        pitches_dropdown.value = ""
        pitches_dropdown.options = self.get_pitches()
        
        # Update thread type options based on metric mode
        thread_type_dropdown = self.ids.thread_type_dropdown
        thread_type_dropdown.options = self.get_thread_types()
        # Reset to first available type
        first_type = self.get_thread_types()[0] if self.get_thread_types() else ThreadType.ISO_METRIC.value
        thread_type_dropdown.value = first_type
        self.assistedThreadingBar.thread_profile_type = ThreadType(first_type)
        
        log.info(f"Metric mode changed to: {value}")
        
    def on_pitch_selected(self, index, selected_pitch):
        self.assistedThreadingBar.selected_pitch = selected_pitch
        self.update_feeds_ratio(index)
        log.info(f"Selected pitch: {selected_pitch}")
    
    def on_thread_type_selected(self, value):
        """Handle thread type selection."""
        try:
            # Convert string value back to ThreadType enum
            thread_type = ThreadType(value)
            self.assistedThreadingBar.thread_profile_type = thread_type
            log.info(f"Selected thread type: {thread_type}")
        except ValueError:
            log.warning(f"Invalid thread type value: {value}")
        
    def update_feeds_ratio(self, index):
        ratio = self.current_feeds_table[index].ratio
        spindle_scale: CoordBar = self.assistedThreadingBar.app.get_spindle_scale()
        if spindle_scale is not None:
            spindle_scale.syncRatioNum = ratio.numerator
            spindle_scale.syncRatioDen = ratio.denominator
        log.info(f"Configured ratio is: {ratio.numerator}/{ratio.denominator}")