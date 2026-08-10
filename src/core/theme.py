"""Named accent themes (seed colors) for the app shell.

Theme selection is stored in the config as a simple name string. The
registry maps names to Material 3 seed colors so the rest of the app
never has to know about concrete color values.

Kept free of ``flet`` imports so core logic stays headless-testable.
"""

#: Default theme applied when the config carries no explicit choice.
DEFAULT_THEME = "purple"

#: Ordered registry: theme name -> (human label, Material 3 seed color).
#: The first entry is the default.
THEMES: dict[str, tuple[str, str]] = {
    "purple": ("Purple", "#7C4DFF"),
    "blue": ("Blue", "#2196F3"),
    "teal": ("Teal", "#009688"),
    "green": ("Green", "#4CAF50"),
    "orange": ("Orange", "#FF9800"),
    "red": ("Red", "#F44336"),
    "pink": ("Pink", "#E91E63"),
}


def theme_names() -> list[str]:
    """Return theme names in display order."""
    return list(THEMES)


def theme_label(name: str) -> str:
    """Return the human label for a theme name."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])[0]


def theme_seed(name: str) -> str:
    """Return the Material 3 seed color for a theme name.

    Unknown names fall back to the default theme so a stale config value
    never breaks startup.
    """
    return THEMES.get(name, THEMES[DEFAULT_THEME])[1]


def theme_is_valid(name: str) -> bool:
    """Return whether *name* matches a registered theme."""
    return name in THEMES
