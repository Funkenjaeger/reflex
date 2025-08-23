from kivy.logger import Logger

log = Logger.getChild(__name__)

class AssistedThreadingWizard:
    def __init__(self, bar):
        self.bar = bar
        self.app = bar.app
        self.current_step = 0
        self._current_callback = None
        self.steps = [
            self.step_1_initial_position,
            self.step_2_stop_position,
            # ... same steps as before ...
        ]

    def start(self):
        self.goto_step(0)

    def reset_ui(self):
        # Reset wizard_area to default content
        self.bar.label_text = ""
        self.bar.display_value = ""

    def goto_step(self, index):
        self.current_step = index
        if 0 <= index < len(self.steps):
            self.steps[index]()
        else:
            log.info("Wizard finished")
            self.bar.is_running = False
    
    def goto_next_step(self, *args):
        if self._current_callback:
            self._current_callback(*args)  
        self.goto_step(self.current_step + 1)

    def set_instruction(self, label_text, next_button_text, next_button_callback):
        self.bar.label_text = label_text
        self.bar.next_button_text = next_button_text
        self._current_callback = next_button_callback
    

    def step_1_initial_position(self):
        self.set_instruction("Go to initial Z and press Set", "Set", self._capture_initial_position)
        self.bar.bind_to_scale(self.app.scales[self.bar.selected_saddle_scale_id])

    def step_2_stop_position(self):
        self.set_instruction("Go to stop Z and press Set", "Set", self._capture_stop_position)
        self.bar.bind_to_scale(self.app.scales[self.bar.selected_saddle_scale_id])
        
    def _capture_initial_position(self, *args):
        self.bar.start_position = self.app.scales[self.bar.selected_saddle_scale_id].encoderCurrent
        log.info(f"Initial position set to: {self.bar.start_position}")
        
    def _capture_stop_position(self, *args):
        self.bar.stop_position = self.app.scales[self.bar.selected_saddle_scale_id].encoderCurrent    
        log.info(f"Stop position set to: {self.bar.stop_position}")
    
        #self.bar.display_value = self.app.servo.formattedPosition