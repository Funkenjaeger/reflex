"""Theme ``[paths]`` values must stay inside the package.

Every font and the logo are vendored in ``reflex/fonts`` / ``reflex/pictures``
and referenced repo-relatively, which is what lets a replay resolve
byte-identical assets by construction. A user theme
(``~/.config/reflex/themes/*.ini``) was the one place a path could point
elsewhere.
"""

import pytest

from reflex.components.widgets import palettes


@pytest.mark.parametrize("value", [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/etc/passwd",
    "C:/Windows/Fonts/arial.ttf",
    "../../../../usr/share/fonts/x.ttf",
    "",
])
def test_paths_outside_the_package_are_rejected(value):
    assert palettes._safe_asset_path("evil", "font_bold", value) is None


@pytest.mark.parametrize("value,expected", [
    ("fonts/Manrope-Bold.ttf", "fonts/Manrope-Bold.ttf"),
    ("./fonts/Manrope-Bold.ttf", "fonts/Manrope-Bold.ttf"),
    ("pictures/reflex_logo.png", "pictures/reflex_logo.png"),
    ("fonts/sub/../Manrope-Bold.ttf", "fonts/Manrope-Bold.ttf"),
])
def test_repo_relative_paths_are_kept(value, expected):
    assert palettes._safe_asset_path("ok", "font_bold", value) == expected


def test_builtin_themes_all_use_repo_relative_assets():
    """If this ever fails, replaying a capture stops being portable."""
    for name, palette in palettes.PALETTES.items():
        for token in palettes.PATH_TOKENS:
            value = palette[token]
            assert not value.startswith("/"), f"{name}.{token} is absolute"
            assert not value.startswith(".."), f"{name}.{token} escapes package"


def test_a_rejected_path_falls_back_rather_than_breaking_the_theme(tmp_path):
    """A partial or bad user file still loads -- the module's stated contract."""
    theme = tmp_path / "custom.ini"
    theme.write_text(
        "[meta]\nname = custom\n"
        "[paths]\nfont_bold = /usr/share/fonts/nope.ttf\n"
        "font_mono = fonts/ShareTechMono-Regular.ttf\n",
        encoding="utf-8",
    )
    palette, _seeds = palettes._load_file(str(theme), "custom")
    assert "font_bold" not in palette, "unsafe value must be dropped, not kept"
    assert palette["font_mono"] == "fonts/ShareTechMono-Regular.ttf"
