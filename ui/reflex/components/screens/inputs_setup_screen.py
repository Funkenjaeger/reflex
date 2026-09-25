from kivy.logger import Logger
from kivy.factory import Factory
from kivy.uix.screenmanager import Screen

from reflex.utils.input_axis_map import input_axis_labels
from reflex.components.widgets import facelift_chrome  # noqa: F401 -- defines <SetupButton>/<ThemedLabel>
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

ITEMS_PER_PAGE = 12  # 4 cols x 3 rows


class InputsSetupScreen(Screen):

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self._page = 0
        super().__init__(**kv)
        self._rebuild_buttons()

    def _rebuild_buttons(self):
        container = self.ids.inputs_container
        container.clear_widgets()

        items = list(enumerate(self.app.inputs))
        total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        self._page = min(self._page, total_pages - 1)

        start = self._page * ITEMS_PER_PAGE
        page_items = items[start:start + ITEMS_PER_PAGE]

        # Annotate each button with the axis it feeds. Read-only: this screen
        # still does not assign anything, it just stops the operator having to
        # drill into Axes to find out what an input is for. Blank when no
        # provisioned axis claims it -- an unused input on a four-input board
        # is ordinary and does not need announcing.
        labels = input_axis_labels(self.app.axes)

        for i, scale in page_items:
            axis = labels.get(i, "")
            btn = Factory.SetupButton(text=f"Input {i}\n{axis}" if axis else f"Input {i}",
                                      font_size=22, halign="center")
            btn.bind(on_release=lambda _, idx=i: self._goto_input(idx))
            container.add_widget(btn)

        self._update_footer(total_pages)

    def _update_footer(self, total_pages):
        footer = self.ids.page_footer
        footer.clear_widgets()
        if total_pages <= 1:
            footer.height = 0
            return
        footer.height = 60
        # Themed, not stock: a stock disabled Button (Previous on page 1, Next
        # on the last page) draws white at 30%, 1.30:1 on the light theme,
        # and a stock Label draws white on the light page (1.38:1).
        prev_btn = Factory.SetupButton(text="Previous", font_size=22, disabled=self._page == 0)
        prev_btn.bind(on_release=lambda _: self._change_page(-1))
        page_label = Factory.ThemedLabel(text=f"Page {self._page + 1} / {total_pages}", font_size=22)
        next_btn = Factory.SetupButton(text="Next", font_size=22, disabled=self._page >= total_pages - 1)
        next_btn.bind(on_release=lambda _: self._change_page(1))
        footer.add_widget(prev_btn)
        footer.add_widget(page_label)
        footer.add_widget(next_btn)

    def _change_page(self, delta):
        self._page += delta
        self._rebuild_buttons()

    def _goto_input(self, index):
        screen_name = f"input_{index}"
        if self.app.manager.has_screen(screen_name):
            self.app.manager.goto(screen_name)
        else:
            log.warning(f"Screen {screen_name} not found")

    def on_pre_enter(self, *args):
        self._rebuild_buttons()
