from kivy.logger import Logger
from kivy.properties import NumericProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

class JogBar(BoxLayout):
    desired_speed = NumericProperty(0)
    enable_jog = BooleanProperty(False)
    enable_jog_reverse = BooleanProperty(False)

    def __init__(self, **kwargs):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kwargs)
        self.bind(desired_speed=self.update_jog)
        self.bind(enable_jog=self.update_jog)
        self.bind(enable_jog_reverse=self.update_jog)

    def update_jog(self, instance, value):
        if self.desired_speed > self.app.servo.maxSpeed:
            self.desired_speed = self.app.servo.maxSpeed
        if self.desired_speed < -self.app.servo.maxSpeed:
            self.desired_speed = -self.app.servo.maxSpeed

        # The jog buttons mean PHYSICAL carriage direction. jogSpeed drives
        # the commanded-step counter, and the firmware's servoDir only flips
        # the DIR pin — sign(carriage) = sign(jogSpeed) × servoDir (pinned by
        # tests/system/test_jog_mode.py). Without this term, a machine
        # commissioned with reverse=True jogs backwards against its buttons —
        # which elspi did for its entire life until 2026-08-17, unnoticed
        # because jog was never used.
        direction = -1 if self.app.servo.reverse else 1

        # Forward
        if self.enable_jog:
            self.app.servo.jogSpeed = self.desired_speed * direction
            self.app.servo.servoMode = 2

        # Reverse
        if self.enable_jog_reverse:
            self.app.servo.jogSpeed = -self.desired_speed * direction
            self.app.servo.servoMode = 2

        # Idle
        if not self.enable_jog_reverse and not self.enable_jog:
            self.app.servo.jogSpeed = 0
