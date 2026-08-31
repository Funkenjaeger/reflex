from kivy.uix.screenmanager import ScreenManager, FadeTransition
from kivy.properties import ListProperty
from kivy.logger import Logger
log = Logger.getChild(__name__)


class Manager(ScreenManager):
    previous = ListProperty()
    transition = FadeTransition()

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        super().__init__(**kv)
        self.transition.duration = .05

        from reflex.components.screens.home_screen import HomePage
        self.add_widget(HomePage(name="home"))

        from reflex.components.screens.setup_screen import SetupScreen
        self.add_widget(SetupScreen(name="setup_screen"))

        from reflex.components.screens.machine_screen import MachineScreen
        self.add_widget(MachineScreen(name="machine"))

        from reflex.components.screens.network_screen import NetworkScreen
        self.add_widget(NetworkScreen(name="network"))

        from reflex.components.screens.formats_screen import FormatsScreen
        self.add_widget(FormatsScreen(name="formats"))

        # Add screen for color picker
        from reflex.components.screens.color_picker_screen import ColorPickerScreen
        self.app.color_picker = ColorPickerScreen(name="color_picker")
        self.add_widget(self.app.color_picker)

        # Add screen for font picker
        from reflex.components.screens.font_picker_screen import FontPickerScreen
        self.app.font_picker = FontPickerScreen(name="font_picker")
        self.add_widget(self.app.font_picker)

        # Add inputs listing screen and individual scale screens
        from reflex.components.screens.inputs_setup_screen import InputsSetupScreen
        self.add_widget(InputsSetupScreen(name="inputs_setup"))

        from reflex.components.screens.input_screen import InputScreen
        for i in range(len(self.app.inputs)):
            self.add_widget(InputScreen(name=f"input_{i}", input=self.app.inputs[i]))

        # Add axes configuration screens
        from reflex.components.screens.axes_setup_screen import AxesSetupScreen
        self.add_widget(AxesSetupScreen(name="axes_setup"))

        from reflex.components.screens.axis_screen import AxisScreen
        for ax in self.app.axes:
            self.add_widget(AxisScreen(name=f"axis_{ax.id_override}", axis=ax))

        # Add screen for servo setup
        from reflex.components.screens.servo_screen import ServoScreen
        self.add_widget(ServoScreen(name="servo", servo=self.app.servo))

        # Add screen for ELS setup
        from reflex.components.screens.els_setup_screen import ElsSetupScreen
        self.add_widget(ElsSetupScreen(name="els_setup", els=self.app.els))

        # UpdateScreen is deliberately NOT registered for 1.1.0. It queried
        # the ARCHIVED Funkenjaeger/reflex-ui (so it offered v1.0.0) and
        # installed into /reflex-ui, deleted at the monorepo cutover -- and a
        # UI-only updater no longer fits releases that are lockstep fw+ui
        # pairs needing an ST-Link for the firmware half. The screen is kept
        # in the tree as the starting point for that redesign; nothing reaches
        # it. See tests/components/test_update_screen_unwired.py.

        from reflex.components.screens.system_screen import SystemScreen
        self.add_widget(SystemScreen(name="system"))

        from reflex.components.screens.logs_screen import LogsScreen
        self.add_widget(LogsScreen(name="logs"))

        from reflex.components.screens.log_viewer_screen import LogViewerScreen
        self.app.log_viewer = LogViewerScreen(name="log_viewer")
        self.add_widget(self.app.log_viewer)

        from reflex.components.screens.profiling_screen import ProfilingScreen
        self.add_widget(ProfilingScreen(name="profiling"))

        # Add screen for plot view
        from reflex.components.plot.plot_screen import PlotScreen
        self.add_widget(PlotScreen(name="plot"))
        self.current = "home"

    def set_previous(self, instance, value):
        self.previous.append(value)
        log.info(f"Previous history: {self.previous}")

    def back(self):
        # self.manager.transition.mode = "pop"
        self.current = self.previous.pop()
        log.debug(f"Back array {self.previous}")

    def goto(self, screen: str):
        # self.manager.transition.mode = "push"
        self.previous.append(self.current)
        log.debug(f"Goto array {self.previous}")
        self.current = screen
