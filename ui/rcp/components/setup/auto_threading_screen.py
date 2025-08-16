import os
from typing import List

from kivy.lang import Builder
from kivy.logger import Logger
from kivy.properties import ObjectProperty, ListProperty, NumericProperty, BooleanProperty
from kivy.uix.screenmanager import Screen

from rcp.components.home.automatic_threading_bar import AutomaticThreadingBar
from rcp.components.home.coordbar import CoordBar

log = Logger.getChild(__name__)
kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading KV file: {kv_file}")
    Builder.load_file(kv_file)


class AutoThreadingScreen(Screen):
    automaticThreadingBar: AutomaticThreadingBar = ObjectProperty()
    servo = ObjectProperty()
    scales = ListProperty()
    scales_labels = ListProperty()
    scales_mapping = {}

    def __init__(self, **kv):
        super().__init__(**kv)
        Logger.info("AutoThreadingScreen initialized.")  # Log an info message
        self.update_scales_labels()
    
    def update_scales_labels(self):
        """Update scales_labels and scales_mapping based on the current scales."""
        Logger.debug(f"Updating scales_labels with scales: {self.scales}")  # Log a debug message
        self.scales_labels = [ f"Scale {scale.inputIndex}: {scale.axisName}" for scale in self.scales if isinstance(scale, CoordBar) and not scale.spindleMode]
        
        # Update the mapping
        self.scales_mapping = {
            f"Scale {scale.inputIndex}: {scale.axisName}": scale.inputIndex
            for scale in self.scales if isinstance(scale, CoordBar)
        }
        
        Logger.info(f"Updated scales_labels: {self.scales_labels}")  # Log an info message

    def on_saddle_scale_selected(self, selected_label):
        if selected_label in self.scales_mapping:
            self.automaticThreadingBar.selected_saddle_scale_id = self.scales_mapping[selected_label]
            Logger.info(f"Selected saddle scale: {self.automaticThreadingBar.selected_saddle_scale_id}")
        else:
            Logger.warning(f"Selected label not found in mapping: {selected_label}")

        # Update the other dropdown options dynamically
        cross_dropdown = self.ids.cross_slide_dropdown  # make sure you give an id in KV
        cross_dropdown.options = self.get_cross_slide_scale_options()

    def on_cross_slide_scale_selected(self, selected_label):
        if selected_label in self.scales_mapping:
            self.automaticThreadingBar.selected_cross_slide_scale_id = self.scales_mapping[selected_label]
            Logger.info(f"Selected cross slide scale: {self.automaticThreadingBar.selected_cross_slide_scale_id}")
        else:
            Logger.warning(f"Selected label not found in mapping: {selected_label}")

        # Update the other dropdown options dynamically
        saddle_dropdown = self.ids.saddle_dropdown
        saddle_dropdown.options = self.get_saddle_scale_options()


    def set_reversing_speed(self, val):
        try:
            self.automaticThreadingBar.reversing_speed = min(int(val), self.servo.maxSpeed)
        except ValueError:
            pass
    
    def get_label_for_scale_id(self, scale_id):
        if not self.scales_mapping:
            self.update_scales_labels()
        for label, sid in self.scales_mapping.items():
            if sid == scale_id:
                return label
        return ""

    def get_saddle_scale_options(self):
        """Return available options for the Saddle Scale dropdown."""
        if not self.scales_labels:
            self.update_scales_labels()
        cross_label = self.get_label_for_scale_id(self.automaticThreadingBar.selected_cross_slide_scale_id)
        return [label for label in self.scales_labels if label != cross_label]

    def get_cross_slide_scale_options(self):
        """Return available options for the Cross Slide Scale dropdown."""
        if not self.scales_labels:
            self.update_scales_labels()
        saddle_label = self.get_label_for_scale_id(self.automaticThreadingBar.selected_saddle_scale_id)
        return [label for label in self.scales_labels if label != saddle_label]


