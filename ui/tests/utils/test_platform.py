from types import SimpleNamespace
from unittest.mock import patch

from reflex.utils.platform import format_bytes


class TestFormatBytes:
    def test_gib(self):
        assert format_bytes(2 * 1024 ** 3) == "2.00 GiB"

    def test_gib_fractional(self):
        assert format_bytes(int(1.5 * 1024 ** 3)) == "1.50 GiB"

    def test_mib(self):
        assert format_bytes(512 * 1024 ** 2) == "512.00 MiB"

    def test_bytes(self):
        assert format_bytes(1023) == "1023 bytes"

    def test_none(self):
        assert format_bytes(None) == "N/A"

    def test_zero(self):
        assert format_bytes(0) == "0 bytes"


class TestSystemScreenFreeSpace:
    """The System screen's one row (2026-09-19: the upstream resize/reboot
    buttons and the device readout were removed)."""

    def _screen(self):
        from reflex.components.screens.system_screen import SystemScreen
        with patch.object(SystemScreen, "apply_class_lang_rules"):
            return SystemScreen()

    def test_free_space_is_read_on_every_visit(self):
        s = self._screen()
        usage = SimpleNamespace(total=0, used=0, free=21 * 1024 ** 3)
        with patch("reflex.components.screens.system_screen.shutil.disk_usage",
                   return_value=usage):
            s.on_pre_enter()
        assert s.free_space_str == "21.00 GiB"

    def test_an_unreadable_path_shows_na(self):
        s = self._screen()
        with patch("reflex.components.screens.system_screen.shutil.disk_usage",
                   side_effect=OSError("gone")):
            s.refresh()
        assert s.free_space_str == "N/A"
