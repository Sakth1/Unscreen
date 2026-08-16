"""Release-notes markdown: sanitize GitHub bodies and render them with flet.

GitHub release bodies arrive as markdown that may contain images, raw HTML
and very long sections. This module strips what cannot render well on every
target (images, HTML tags), caps the length, and builds a themed
:class:`flet.Markdown` control whose links open in the system browser.
"""

from __future__ import annotations

import re

import flet as ft

_MAX_NOTES_CHARS = 6000

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def sanitize_release_notes(markdown: str, max_chars: int = _MAX_NOTES_CHARS) -> str:
    """Make a GitHub release body safe and compact for in-app rendering.

    - Image syntax (``![alt](url)``) is dropped, keeping the alt text.
    - Raw HTML tags are removed (GitHub bodies may carry ``<details>``,
      ``<img>``, ``<br>`` and friends that flet cannot render).
    - Runaway blank lines are collapsed.
    - The result is capped at ``max_chars`` with a trailing ellipsis.
    """
    text = _IMAGE_PATTERN.sub(r"\1", markdown or "")
    text = _HTML_TAG_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _link_handler(page: ft.Page):
    async def _open(event) -> None:
        url = getattr(event, "data", None)
        if url:
            await page.launch_url(url)

    return _open


def build_notes_markdown(page: ft.Page, notes: str) -> ft.Markdown:
    """Build the themed, scrollable release-notes control.

    Uses the GitHub Web extension set so bare URLs (e.g. the "Full
    Changelog" line) are auto-linked. Links open via ``page.launch_url``;
    code blocks pick a highlight theme matching the current theme mode.
    """
    dark = getattr(page, "theme_mode", None) == ft.ThemeMode.DARK
    code_theme = ft.MarkdownCodeTheme.A11Y_DARK if dark else ft.MarkdownCodeTheme.GITHUB
    return ft.Markdown(
        value=notes,
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=code_theme,
        auto_follow_links=False,
        md_style_sheet=ft.MarkdownStyleSheet(
            p_text_style=ft.TextStyle(size=12, color=ft.Colors.ON_SURFACE, height=1.4),
            h1_text_style=ft.TextStyle(
                size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            h2_text_style=ft.TextStyle(
                size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            h3_text_style=ft.TextStyle(
                size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            h4_text_style=ft.TextStyle(
                size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE
            ),
            a_text_style=ft.TextStyle(
                color=ft.Colors.PRIMARY, weight=ft.FontWeight.W_500
            ),
            block_spacing=4,
        ),
        on_tap_link=_link_handler(page),
    )
