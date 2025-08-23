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
    left_hand_thread = BooleanProperty(False)
    inner_thread = BooleanProperty(False)
    
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
        """Bind display_value to a scale's encoderCurrent with strict keypad override support."""

        # Unbind old scale if it exists
        if hasattr(self, "_bound_scale") and self._bound_scale is not None:
            self._bound_scale.unbind(encoderCurrent=self._on_encoder_update)
            self._bound_scale.unbind(formattedPosition=self._on_format_update)

        # Store the scale
        self._bound_scale = scale

        # --- Encoder update handler ---
        def on_encoder_update(instance, value):
            # Cancel manual override if the encoder moves
            if self.wizard and self.wizard.manual_stop_length is not None:
                log.info("Scale encoder moved — discarding manual stop length override")
                self.wizard.manual_stop_length = None
            # Always update display to formattedPosition (not raw encoder!)
            self.display_value = instance.formattedPosition

        # --- Format update handler ---
        def on_format_update(instance, value):
            # Only update display if NOT in manual override
            if not (self.wizard and self.wizard.manual_stop_length is not None):
                self.display_value = value

        # Keep references so we can unbind later
        self._on_encoder_update = on_encoder_update
        self._on_format_update = on_format_update

        # Bind both
        scale.bind(encoderCurrent=on_encoder_update)
        scale.bind(formattedPosition=on_format_update)

        # Initial display
        self.display_value = scale.formattedPosition


    def bind_to_value_button(self, on_release_fn):
        """Bind the value button to a function."""
         # Unbind old function if it exists
        if hasattr(self, "_on_value_button_release") and self._on_value_button_release is not None:
            self.ids.btn_value.unbind(on_release=self._on_value_button_release)

        # Store the binding function
        self._on_value_button_release = on_release_fn
        
        if(on_release_fn is None):
            # If None is passed, disable the button
            self.ids.btn_value.disabled = True
            return
        
        self.ids.btn_value.disabled = False
        # Bind the new function
        self.ids.btn_value.bind(on_release=on_release_fn)

    def _update_display_value(self, instance, value):
        self.display_value = value