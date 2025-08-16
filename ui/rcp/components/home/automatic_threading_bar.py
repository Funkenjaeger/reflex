import collections
import time
import os

from typing import List
from fractions import Fraction

from kivy.lang import Builder
from kivy.logger import Logger
from kivy.properties import ObjectProperty, ListProperty, NumericProperty, BooleanProperty

from rcp.components.home.coordbar import CoordBar
from rcp.components.home.servobar import ServoBar
from rcp.dispatchers import SavingDispatcher
from rcp.components.keypad import Keypad
from rcp.utils.ctype_calc import uint32_subtract_to_int32

log = Logger.getChild(__name__)

kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading KV file: {kv_file}")
    Builder.load_file(kv_file)


class AutomaticThreadingBar(SavingDispatcher):    
    selected_cross_slide_scale_id = NumericProperty(0)
    selected_saddle_scale_id = NumericProperty(1)
    cross_slide_diameter_mode = BooleanProperty(True)
    
    reversing_speed = NumericProperty(500)
    metric_mode = BooleanProperty(True)
    backlash_retraction_distance = NumericProperty(10)
    backlash_cusion = NumericProperty(2)
    cross_slide_retraction_distance = NumericProperty(2)


    disableControls = BooleanProperty(False)
    _skip_save = []

    def __init__(self, **kv):
        from rcp.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
