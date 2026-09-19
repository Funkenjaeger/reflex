import subprocess

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import StringProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv
from reflex.utils.platform import (
    is_raspberry_pi,
    get_root_device,
    parse_disk_and_partition,
    get_block_size_bytes,
    get_filesystem_usage,
    format_bytes,
)

log = Logger.getChild(__name__)
load_kv(__file__)


class SystemScreen(Screen):
    is_pi = BooleanProperty(False)
    root_device = StringProperty("N/A")
    disk_device = StringProperty("N/A")
    partition_number = StringProperty("N/A")
    disk_size_str = StringProperty("N/A")
    partition_size_str = StringProperty("N/A")
    fs_total_str = StringProperty("N/A")
    fs_used_str = StringProperty("N/A")
    fs_free_str = StringProperty("N/A")
    status = StringProperty("")

    def __init__(self, **kv):
        super().__init__(**kv)
        self.is_pi = is_raspberry_pi()
        if self.is_pi:
            Clock.schedule_once(lambda dt: self.refresh_storage_info())

    def refresh_storage_info(self):
        root_dev = get_root_device()
        if not root_dev:
            self.log("Could not determine root device")
            return

        self.root_device = root_dev
        disk, part_num = parse_disk_and_partition(root_dev)

        if disk and part_num:
            self.disk_device = disk
            self.partition_number = part_num
        else:
            self.log(f"Could not parse disk/partition from {root_dev}")
            return

        disk_size = get_block_size_bytes(disk)
        part_size = get_block_size_bytes(root_dev)

        self.disk_size_str = format_bytes(disk_size)
        self.partition_size_str = format_bytes(part_size)

        usage = get_filesystem_usage(root_dev)
        if usage:
            self.fs_total_str = format_bytes(usage["total"])
            self.fs_used_str = format_bytes(usage["used"])
            self.fs_free_str = format_bytes(usage["available"])

    def log(self, message: str):
        log.info(message)
        self.status += f"{message}\n"

    def prompt_reboot(self):
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)

        btn_cancel = Button(text="Cancel", font_size=22)
        btn_confirm = Button(text="Reboot Now", font_size=22)

        content.add_widget(btn_confirm)
        content.add_widget(btn_cancel)

        popup = Popup(
            title="Reboot the system?",
            content=content,
            size_hint=(0.6, 0.4),
            auto_dismiss=False,
        )

        btn_cancel.bind(on_release=popup.dismiss)
        btn_confirm.bind(on_release=lambda _: self._do_reboot(popup))

        popup.open()

    def _do_reboot(self, popup):
        popup.dismiss()
        self.log("Rebooting...")
        try:
            subprocess.Popen(["sudo", "reboot"])
        except Exception as e:
            self.log(f"Reboot failed: {e}")
