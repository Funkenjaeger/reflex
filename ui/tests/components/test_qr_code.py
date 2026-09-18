"""QrCode: the Backup screen's QR of the device-flow verification URL.

The pixel-level check (the drawn frame matches segno's matrix module for
module) lives in previews/preview_setup_screen.py, which renders at the
lathe's 1024x600; these pin the geometry and the matrix it paints from.
"""
import segno

from reflex.components.widgets.qr_code import QUIET_ZONE, QrCode

URL = "https://github.com/login/device"


def test_modules_are_segnos_matrix_with_the_quiet_zone():
    qr = QrCode(data=URL)
    expected = [[bool(v) for v in row]
                for row in segno.make(URL, error="m", micro=False).matrix_iter(scale=1, border=QUIET_ZONE)]
    assert qr.modules() == expected
    assert not any(qr.modules()[0]), "top quiet-zone row is all light"
    assert qr.modules()[QUIET_ZONE][QUIET_ZONE], "finder pattern corner is dark"


def test_geometry_uses_whole_pixel_modules_centred():
    qr = QrCode(data=URL, size=(190, 190), pos=(10, 20))
    module, x0, y_top, n = qr.geometry()
    assert module == 190 // n and module * n <= 190
    assert x0 == int(10 + (190 - module * n) / 2)


def test_nothing_is_drawn_without_data_or_room():
    assert QrCode(data="").geometry() is None
    assert QrCode(data=URL, size=(10, 10)).geometry() is None, "under 1 px per module"
