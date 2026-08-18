"""Log-file ordering: newest first BY NAME, not by mtime.

Machine power is common to the Pi and the controller, so the kiosk loses
power mid-write routinely — and journal replay can leave the dead log's
mtime NEWER than the live session's file. That is how the current log
sorted second in the browser on 2026-08-17. The filename's date + run
counter is ground truth kivy itself maintains; mtime is only a fallback
for names that do not parse.
"""
from reflex.components.setup.logs_panel import _log_sort_key


def _sorted_names(names):
    return sorted(names, key=_log_sort_key, reverse=True)


def test_same_day_run_counter_orders_newest_first_regardless_of_mtime():
    # No filesystem involved for parsed names — mtime cannot influence them.
    assert _sorted_names(
        ["/var/log/kivy_26-08-17_0.txt", "/var/log/kivy_26-08-17_1.txt"]
    ) == ["/var/log/kivy_26-08-17_1.txt", "/var/log/kivy_26-08-17_0.txt"]


def test_dates_outrank_run_counters():
    assert _sorted_names(
        ["/var/log/kivy_26-08-16_9.txt", "/var/log/kivy_26-08-17_0.txt"]
    ) == ["/var/log/kivy_26-08-17_0.txt", "/var/log/kivy_26-08-16_9.txt"]


def test_double_digit_run_counters_sort_numerically():
    assert _sorted_names(
        ["/var/log/kivy_26-08-17_2.txt", "/var/log/kivy_26-08-17_10.txt"]
    ) == ["/var/log/kivy_26-08-17_10.txt", "/var/log/kivy_26-08-17_2.txt"]


def test_unparseable_names_sort_below_real_logs(tmp_path):
    stray = tmp_path / "kivy_backup.txt"
    stray.write_text("x")
    out = _sorted_names([str(stray), "/var/log/kivy_26-08-01_0.txt"])
    assert out[0] == "/var/log/kivy_26-08-01_0.txt"
