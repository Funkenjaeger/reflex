import os
from kivy.logger import Logger
from kivy.properties import StringProperty, ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.lang import Builder

log = Logger.getChild(__name__)
kv_file = os.path.join(os.path.dirname(__file__), __file__.replace(".py", ".kv"))
if os.path.exists(kv_file):
    log.info(f"Loading KV file {kv_file}")
    Builder.load_file(kv_file)


class CustomPopup(BoxLayout):
    title = StringProperty("")
    message = StringProperty("")
    button_text = StringProperty("OK")
    # When non-empty, a second (cancel) button is shown alongside the primary
    # button, turning the dialog into a confirm/cancel. Cancel just dismisses;
    # the primary button fires confirm_callback (the confirm action).
    cancel_text = StringProperty("")
    # This property must NOT be named with an "on_" prefix. Kivy's
    # Widget.__init__ strips every kwarg starting with "on_" out of kwargs and
    # hands it to self.bind() as an EVENT handler, so an `on_*=callable` kwarg
    # never reaches the property — it silently becomes a property-change
    # listener instead. That is exactly what broke the ELS "Enable Feed"
    # confirm dialog: the primary button had no callback and behaved
    # identically to Cancel.
    confirm_callback = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        # Accept (and repair) the old `on_dismiss_callback` spelling loudly
        # rather than letting Kivy swallow it silently a second time.
        legacy = kwargs.pop("on_dismiss_callback", None)
        if legacy is not None:
            log.warning("CustomPopup: 'on_dismiss_callback' is deprecated — use "
                        "'confirm_callback' (an on_* kwarg is consumed by Kivy "
                        "as an event binding and never reaches the property)")
            kwargs.setdefault("confirm_callback", legacy)
        super().__init__(**kwargs)
        self._popup = Popup(
            title=self.title,
            content=self,
            size_hint=(0.6, 0.5),
            auto_dismiss=False,
        )

    def open(self):
        self._popup.title = self.title
        self._popup.open()

    def dismiss(self):
        self._popup.dismiss()

    def on_button_press(self):
        if self.confirm_callback:
            self.confirm_callback()
        self.dismiss()

    def on_cancel_press(self):
        # Cancel just closes the dialog without firing the confirm callback.
        self.dismiss()