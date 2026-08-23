"""Tests for icon resolution (site favicons, Windows exe icons, Android package icons).

The fallback chain under test: platform icon -> site favicon -> ``None``,
where ``None`` means the caller keeps the colored-initial avatar.
"""

import struct
import urllib.request
import zlib

from core.icons import icon_resolver
from core.icons.icon_resolver import (
    exe_icon_png,
    fetch_site_favicon,
    ico_to_png,
    is_site_bucket,
    package_icon_png,
    site_key_to_domain,
)

# ---------------------------------------------------------------------------
# PNG decode/encode helpers (pure Python; Pillow is e2e-only in this repo).
# ---------------------------------------------------------------------------


def _encode_png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Minimal PNG decoder: returns (width, height, top-down RGBA rows)."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos, width, height, idat = 8, 0, 0, bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack_from(
                ">IIBB", payload, 0
            )
            assert (bit_depth, color_type) == (8, 6)
        elif tag == b"IDAT":
            idat += payload
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    out = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        if f == 1:
            for x in range(4, stride):
                row[x] = (row[x] + row[x - 4]) & 0xFF
        elif f == 2:
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = row[x - 4] if x >= 4 else 0
                row[x] = (row[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = row[x - 4] if x >= 4 else 0
                b = prev[x]
                c = prev[x - 4] if x >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[x] = (row[x] + pr) & 0xFF
        out += row
        prev = row
    return width, height, bytes(out)


# ---------------------------------------------------------------------------
# ICO builders (the ICO-flavored DIB format: doubled height + AND mask).
# ---------------------------------------------------------------------------


def _mask(size: int, transparent: set[tuple[int, int]] | None = None) -> bytes:
    transparent = transparent or set()
    mask_stride = ((size + 31) // 32) * 4
    rows = bytearray()
    for y in range(size):
        row = bytearray(mask_stride)
        for x in range(size):
            if (y, x) in transparent:
                row[x // 8] |= 1 << (7 - x % 8)
        rows += row
    return bytes(rows)


def _dib32(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = bytearray()
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            row += bytes((b, g, r, a))
        rows += row
    return header + bytes(rows) + _mask(size)


def _dib8(size: int, indices: list[int], palette: list[tuple[int, int, int]]) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 8, 0, 0, 0, 0, 256, 256)
    colors = palette + [(0, 0, 0)] * (256 - len(palette))
    palette_bytes = b"".join(bytes((b, g, r, 0)) for r, g, b, *_ in colors)
    stride = (size + 3) & ~3
    rows = bytearray()
    for y in range(size):
        row = bytearray(indices[y * size : (y + 1) * size])
        row += b"\x00" * (stride - size)
        rows += row
    return header + palette_bytes + bytes(rows) + _mask(size)


def _dib4(size: int, indices: list[int], palette: list[tuple[int, int, int]]) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 4, 0, 0, 0, 0, len(palette), 0
    )
    palette_bytes = b"".join(bytes((b, g, r, 0)) for r, g, b, *_ in palette)
    stride = ((size + 1) // 2 + 3) & ~3
    rows = bytearray()
    for y in range(size):
        row = bytearray(stride)
        for x in range(size):
            idx = indices[y * size + x]
            if x % 2 == 0:
                row[x // 2] |= idx << 4
            else:
                row[x // 2] |= idx & 0x0F
        rows += row
    return header + palette_bytes + bytes(rows) + _mask(size)


def _make_ico(entries: list[tuple[int, int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    out = bytearray(header)
    for w, h, data in entries:
        out += struct.pack(
            "<BBBBHHII",
            0 if w == 256 else w,
            0 if h == 256 else h,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
    for _w, _h, data in entries:
        out += data
    return bytes(out)


# ---------------------------------------------------------------------------
# site_key_to_domain / is_site_bucket
# ---------------------------------------------------------------------------


class TestSiteKeyToDomain:
    def test_known_site_maps_to_domain(self):
        assert site_key_to_domain("browser:youtube") == "youtube.com"
        assert site_key_to_domain("browser:github") == "github.com"
        assert site_key_to_domain("browser:stack overflow") == "stackoverflow.com"
        assert site_key_to_domain("browser:x") == "x.com"

    def test_general_browser_bucket_has_no_domain(self):
        assert site_key_to_domain("browser") is None

    def test_unknown_site_has_no_domain(self):
        assert site_key_to_domain("browser:notasite") is None

    def test_regular_app_key_has_no_domain(self):
        assert site_key_to_domain("brave.exe") is None


class TestIsSiteBucket:
    def test_site_bucket_true(self):
        assert is_site_bucket("browser:youtube") is True

    def test_general_browser_bucket_false(self):
        assert is_site_bucket("browser") is False

    def test_unknown_site_false(self):
        assert is_site_bucket("browser:notasite") is False

    def test_regular_app_false(self):
        assert is_site_bucket("brave.exe") is False


# ---------------------------------------------------------------------------
# ico_to_png
# ---------------------------------------------------------------------------


class TestIcoToPng:
    def test_prefers_largest_entry(self):
        red = (255, 0, 0, 255)
        green = (0, 255, 0, 255)
        blue = (0, 0, 255, 255)
        # 2x2 entry and a larger 3x3 entry; the 3x3 must win.
        small = _dib32(2, [red, blue, blue, red])
        big = _dib32(
            3,
            [
                red,
                red,
                red,
                green,
                green,
                green,
                blue,
                blue,
                blue,
            ],
        )
        png = ico_to_png(_make_ico([(2, 2, small), (3, 3, big)]))
        assert png is not None and png.startswith(b"\x89PNG")
        width, height, rgba = _decode_png(png)
        assert (width, height) == (3, 3)
        # DIB rows are bottom-up: the buffer's last row is the image top.
        assert rgba[0:4] == bytes(blue)
        assert rgba[12:16] == bytes(green)
        assert rgba[24:28] == bytes(red)

    def test_32bpp_pixel_and_alpha_round_trip(self):
        red = (255, 0, 0, 255)
        blue = (0, 0, 255, 255)
        dib = _dib32(2, [red, blue, blue, red])
        png = ico_to_png(_make_ico([(2, 2, dib)]))
        assert png is not None
        _w, _h, rgba = _decode_png(png)
        assert rgba[0:4] == bytes(blue)
        assert rgba[4:8] == bytes(red)
        assert rgba[8:12] == bytes(red)
        assert rgba[12:16] == bytes(blue)

    def test_8bpp_palette_entry(self):
        red = (255, 0, 0, 255)
        green = (0, 255, 0, 255)
        dib = _dib8(2, [0, 1, 1, 0], [red, green])
        png = ico_to_png(_make_ico([(2, 2, dib)]))
        assert png is not None
        _w, _h, rgba = _decode_png(png)
        # Bottom-up rows: buffer row [1, 0] renders as the image top row.
        assert rgba[0:4] == bytes(green)
        assert rgba[4:8] == bytes(red)

    def test_4bpp_palette_entry(self):
        red = (255, 0, 0, 255)
        green = (0, 255, 0, 255)
        dib = _dib4(2, [0, 1, 0, 1], [red, green])
        png = ico_to_png(_make_ico([(2, 2, dib)]))
        assert png is not None
        _w, _h, rgba = _decode_png(png)
        assert rgba[0:4] == bytes(red)
        assert rgba[4:8] == bytes(green)

    def test_and_mask_provides_transparency_for_palette_entries(self):
        red = (255, 0, 0, 255)
        dib = _dib8(
            2,
            [0, 0, 0, 0],
            [red],
        )  # mask overrides: opaque pixel gets alpha 255
        png = ico_to_png(_make_ico([(2, 2, dib)]))
        assert png is not None
        _w, _h, rgba = _decode_png(png)
        assert rgba[3] == 255

    def test_embedded_png_entry_passes_through(self):
        png_bytes = _encode_png(2, 2, bytes([255, 0, 0, 255]) * 4)
        result = ico_to_png(_make_ico([(2, 2, png_bytes)]))
        assert result == png_bytes

    def test_unsupported_bpp_returns_none(self):
        header = struct.pack("<IiiHHIIiiII", 40, 2, 4, 1, 1, 0, 8, 0, 0, 2, 2)
        dib = header + b"\x00" * 16
        assert ico_to_png(_make_ico([(2, 2, dib)])) is None

    def test_garbage_returns_none(self):
        assert ico_to_png(b"") is None
        assert ico_to_png(b"not an icon") is None
        assert ico_to_png(b"\x00\x00\x01\x00" + b"\x00" * 20) is None

    def test_truncated_entry_returns_none(self):
        dib = _dib32(2, [(255, 0, 0, 255)] * 4)
        ico = _make_ico([(2, 2, dib)])
        assert ico_to_png(ico[:-6]) is None


# ---------------------------------------------------------------------------
# fetch_site_favicon
# ---------------------------------------------------------------------------


class TestFetchSiteFavicon:
    def test_unknown_or_general_key_skips_fetch(self, monkeypatch):
        calls = []

        def fake_urlopen(*_a, **_k):
            calls.append(1)
            raise AssertionError("should not fetch")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert fetch_site_favicon("browser") is None
        assert fetch_site_favicon("browser:notasite") is None
        assert fetch_site_favicon("brave.exe") is None
        assert calls == []

    def test_png_response_passes_through(self, monkeypatch):
        png = _encode_png(2, 2, bytes([255, 0, 0, 255]) * 4)

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, _n):
                return png

        monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
        assert fetch_site_favicon("browser:youtube") == png

    def test_ico_response_converts_to_png(self, monkeypatch):
        red = (255, 0, 0, 255)
        ico = _make_ico([(2, 2, _dib32(2, [red, red, red, red]))])

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, _n):
                return ico

        monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
        png = fetch_site_favicon("browser:youtube")
        assert png is not None and png.startswith(b"\x89PNG")
        _w, _h, rgba = _decode_png(png)
        assert rgba[0:4] == bytes(red)

    def test_webp_response_returns_none(self, monkeypatch):
        webp = b"RIFF" + b"\x00" * 100 + b"WEBP" + b"\x00" * 100

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, _n):
                return webp

        monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
        assert fetch_site_favicon("browser:youtube") is None

    def test_network_error_returns_none(self, monkeypatch):
        def fake_urlopen(*_a, **_k):
            raise OSError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert fetch_site_favicon("browser:youtube") is None

    def test_corrupt_png_returns_none(self, monkeypatch):
        corrupt = _encode_png(2, 2, bytes([255, 0, 0, 255]) * 4)
        corrupt = corrupt[:-8] + b"\x00" * 8

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, _n):
                return corrupt

        monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
        assert fetch_site_favicon("browser:youtube") is None

    def test_png_encoder_round_trips_via_decoder(self):
        rgba = b"".join(
            bytes((r, g, b, a))
            for (r, g, b), a in [
                ((255, 0, 0), 255),
                ((0, 255, 0), 128),
                ((0, 0, 255), 64),
                ((10, 20, 30), 0),
            ]
        )
        png = icon_resolver._png_from_rgba(rgba, 2, 2)
        _w, _h, decoded = _decode_png(png)
        assert decoded == rgba


# ---------------------------------------------------------------------------
# package_icon_png (Android)
# ---------------------------------------------------------------------------


class TestPackageIconPng:
    def test_returns_none_off_android(self):
        """Off-Android, get_activity() returns None so the function bails out."""
        result = package_icon_png("com.google.android.youtube")
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        """Any jnius/activity error collapses to None."""
        from unittest.mock import MagicMock, patch

        # Mock get_activity to return a non-None value, then make jnius fail
        mock_activity = MagicMock()
        monkeypatch.setattr(
            "utils.android.get_activity",
            lambda: mock_activity,
        )
        # Simulate jnius import failure
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "jnius":
                raise ImportError("no jnius")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert package_icon_png("com.example.app") is None


# ---------------------------------------------------------------------------
# exe_icon_png (Windows)
# ---------------------------------------------------------------------------


class TestExeIconPng:
    def test_returns_none_for_missing_exe(self):
        """Missing exe path returns None gracefully."""
        result = exe_icon_png("C:\\nonexistent\\fake.exe")
        assert result is None

    def test_returns_none_off_windows(self):
        """Off-Windows, _ensure_win_dlls() returns False so the function bails out."""
        # On non-Windows, _ensure_win_dlls always returns False
        result = exe_icon_png("/usr/bin/ls")
        assert result is None

    def test_returns_none_on_bad_path(self):
        """Empty or garbage path returns None."""
        assert exe_icon_png("") is None
        assert exe_icon_png("\x00\x00\x00") is None
