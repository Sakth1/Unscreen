"""Release-notes markdown: sanitization and flet.Markdown construction."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import flet as ft
from sweep_helpers import mock_page

from UI.markdown_notes import (
    _link_handler,
    build_notes_markdown,
    sanitize_release_notes,
)


class TestSanitizeReleaseNotes:
    def test_strips_images_keeps_alt_text(self):
        md = "Before ![screenshot](https://img.example.com/a.png) after"
        assert sanitize_release_notes(md) == "Before screenshot after"

    def test_strips_html_tags(self):
        md = "<details>\n## Notes\n<br>\n</details>\nDone"
        cleaned = sanitize_release_notes(md)
        assert "<details>" not in cleaned
        assert "<br>" not in cleaned
        assert "## Notes" in cleaned

    def test_collapses_runaway_blank_lines(self):
        md = "One\n\n\n\n\n\nTwo"
        assert sanitize_release_notes(md) == "One\n\nTwo"

    def test_caps_length_with_ellipsis(self):
        md = "x" * 200
        assert len(sanitize_release_notes(md, max_chars=100)) == 101
        assert sanitize_release_notes(md, max_chars=100).endswith("…")

    def test_keeps_links_and_bullets(self):
        md = "## What's Changed\n* Fix by @Sakth1 in https://github.com/x/pull/1\n\n**Full Changelog**: https://github.com/x/compare"
        cleaned = sanitize_release_notes(md)
        assert "https://github.com/x/pull/1" in cleaned
        assert "**Full Changelog**" in cleaned
        assert "* Fix by @Sakth1" in cleaned

    def test_empty_input(self):
        assert sanitize_release_notes("") == ""
        assert sanitize_release_notes(None) == ""

    def test_whitespace_only_input(self):
        assert sanitize_release_notes("   \n\n  ") == ""


class TestBuildNotesMarkdown:
    def test_constructs_headless_with_gfm_extensions(self):
        page = mock_page()
        md = build_notes_markdown(page, "# Title\n\n**bold**")
        assert md.extension_set is ft.MarkdownExtensionSet.GITHUB_WEB
        assert md.selectable is True
        assert md.value == "# Title\n\n**bold**"

    def test_uses_dark_code_theme_in_dark_mode(self):
        page = mock_page()
        page.theme_mode = ft.ThemeMode.DARK
        md = build_notes_markdown(page, "text")
        assert md.code_theme is ft.MarkdownCodeTheme.A11Y_DARK

    def test_uses_light_code_theme_by_default(self):
        page = mock_page()
        md = build_notes_markdown(page, "text")
        assert md.code_theme is ft.MarkdownCodeTheme.GITHUB

    def test_link_handler_opens_url(self):
        page = mock_page()
        page.launch_url = AsyncMock()
        handler = _link_handler(page)
        asyncio.run(handler(MagicMock(data="https://example.com")))
        page.launch_url.assert_awaited_once_with("https://example.com")

    def test_link_handler_ignores_empty_data(self):
        page = mock_page()
        page.launch_url = AsyncMock()
        handler = _link_handler(page)
        asyncio.run(handler(MagicMock(data=None)))
        page.launch_url.assert_not_awaited()

    def test_link_handler_is_wired_to_markdown(self):
        page = mock_page()
        md = build_notes_markdown(page, "text")
        assert md.on_tap_link is not None

    def test_notes_are_selectable(self):
        page = mock_page()
        assert build_notes_markdown(page, "x").selectable is True
