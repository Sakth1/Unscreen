from core.theme import (
    DEFAULT_THEME,
    THEMES,
    theme_is_valid,
    theme_label,
    theme_names,
    theme_seed,
)


class TestThemeRegistry:
    def test_default_theme_is_purple(self):
        assert DEFAULT_THEME == "purple"
        assert THEMES[DEFAULT_THEME][0] == "Purple"

    def test_all_seeds_are_hex_colors(self):
        for seed in (entry[1] for entry in THEMES.values()):
            assert seed.startswith("#")
            assert len(seed) == 7
            int(seed[1:], 16)

    def test_theme_names_preserve_order(self):
        names = theme_names()
        assert names == list(THEMES)
        assert names[0] == DEFAULT_THEME

    def test_theme_label_known(self):
        assert theme_label("teal") == "Teal"

    def test_theme_label_unknown_falls_back_to_default(self):
        assert theme_label("neon") == theme_label(DEFAULT_THEME)

    def test_theme_seed_known(self):
        assert theme_seed("teal") == THEMES["teal"][1]

    def test_theme_seed_unknown_falls_back_to_default(self):
        assert theme_seed("neon") == theme_seed(DEFAULT_THEME)

    def test_theme_is_valid(self):
        assert theme_is_valid("blue")
        assert not theme_is_valid("neon")
