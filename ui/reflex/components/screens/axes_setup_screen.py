from kivy.logger import Logger
from kivy.properties import ObjectProperty
from kivy.factory import Factory
from kivy.uix.screenmanager import Screen

from reflex.components.widgets import facelift_chrome  # noqa: F401 -- defines <SetupButton>/<ThemedLabel>
from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)
load_kv(__file__)

ITEMS_PER_PAGE = 12  # 4 cols x 3 rows


class AxesSetupScreen(Screen):
    axes_container = ObjectProperty()

    def __init__(self, **kv):
        from reflex.app import MainApp
        self.app: MainApp = MainApp.get_running_app()
        self._page = 0
        super().__init__(**kv)
        self._rebuild_buttons()

    def _get_all_items(self):
        """Build the list of (label, callback, ink) for all items including the
        Add button. `ink` names a theme colour for the label, or None."""
        items = []
        for i, ax in enumerate(self.app.axes):
            items.append((f"Axis {i}: {ax.axis_name}", lambda _, a=ax: self._goto_axis(a), None))
        items.append(("+ Add Axis", lambda _: self._add_axis(), "success_text"))
        return items

    def _rebuild_buttons(self):
        container = self.ids.axes_container
        container.clear_widgets()

        items = self._get_all_items()
        total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        self._page = min(self._page, total_pages - 1)

        start = self._page * ITEMS_PER_PAGE
        page_items = items[start:start + ITEMS_PER_PAGE]

        for label, callback, ink in page_items:
            btn = Factory.SetupButton(text=label, font_size=22)
            if ink:
                # Add Axis was a green-tinted stock Button; it keeps the green
                # as themed ink. Rebuilt on every entry, so it follows the theme.
                btn.color = getattr(self.app.theme, ink)
            btn.bind(on_release=callback)
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

    def _goto_axis(self, axis):
        screen_name = f"axis_{axis.id_override}"
        if self.app.manager.has_screen(screen_name):
            self.app.manager.goto(screen_name)
        else:
            log.warning(f"Screen {screen_name} not found")

    def _add_axis(self):
        ax = self.app.board.add_axis()
        self.app.axes = list(self.app.board.axes)
        from reflex.components.screens.axis_screen import AxisScreen
        self.app.manager.add_widget(AxisScreen(name=f"axis_{ax.id_override}", axis=ax))
        # Jump to last page to show the new item
        items = self._get_all_items()
        self._page = max(0, (len(items) - 1) // ITEMS_PER_PAGE)
        self._rebuild_buttons()

    def on_pre_enter(self, *args):
        self._rebuild_buttons()
