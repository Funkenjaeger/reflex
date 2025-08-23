import os

from kivy.lang import Builder
from kivy.logger import Logger
from kivy.uix.boxlayout import BoxLayout
from kivy.properties import NumericProperty, BooleanProperty, StringProperty

from rcp.components.home.assisted_threading_settings_popup import AssistedThreadingSettingsPopup
from rcp.components.home.assisted_threading_wizard import AssistedThreadingWizard
from rcp.components.home.coordbar import CoordBar
from rcp.dispatchers import SavingDispatcher

log = Logger.getChild(__name__)

kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading KV file: {kv_file}")
    Builder.load_file(kv_file)


class AssistedThreadingBar(BoxLayout, SavingDispatcher):    
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
    label_text = StringProperty("")
    display_value = StringProperty("")
    next_button_text = StringProperty("")
    start_position = NumericProperty(0)
    stop_position = NumericProperty(0)
    _skip_save = [
        "is_running",
        "label_text",
        "display_value",
        "start_position"
        "stop_position"
        ]

    def __init__(self, **kv):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
        self.wizard = AssistedThreadingWizard(self)
    
    def toggle_is_running(self):
        self.is_running = not self.is_running
        self.app.servo.toggle_enable()
        if self.is_running:
            self.wizard.start()
        else:
            self.wizard.reset_ui()

    def on_wizard_button(self):
        """Called when the right button is pressed."""
        if self.is_running:
            self.wizard.goto_next_step()
        else:
            self.open_settings()
    
    def open_settings(self):
        popup = AssistedThreadingSettingsPopup(assistedThreadingBar=self)
        popup.open()
        
    def bind_to_scale(self, scale: CoordBar):
        """Bind display_value to a scale's formattedPosition."""
        # Unbind old scale if it exists
        if hasattr(self, "_bound_scale") and self._bound_scale is not None:
            self._bound_scale.unbind(formattedPosition=self._update_display_value)

        # Bind new one
        self._bound_scale = scale
        scale.bind(formattedPosition=self._update_display_value)

        # Set immediately
        self.display_value = scale.formattedPosition

    def _update_display_value(self, instance, value):
        self.display_value = value