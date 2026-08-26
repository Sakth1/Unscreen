"""Icon resolution for the dashboard (site favicons, Windows exe icons, Android package icons).

Browser site buckets (``browser:youtube``, ``browser:github``, ...) get a
real icon via DuckDuckGo's favicon endpoint. Windows executables get
their shell icon via ``ExtractIconExW`` (ctypes). Android packages get
their icon via ``PackageManager.getApplicationIcon`` (jnius).

Fallback chain for every row:

    platform icon -> site favicon -> None (colored-initial avatar stays)

DuckDuckGo's ``ip3`` endpoint serves PNG for some domains and ICO
(featuring 32-bpp BMP DIB entries) for others. Flutter cannot decode ICO,
so a pure-Python ICO -> PNG conversion (zlib + struct only, no new
dependencies) is included here; ``fetch_site_favicon`` always returns
PNG bytes or ``None``.
"""

from __future__ import annotations

import logging
import struct
import urllib.request
import zlib

from core.collectors.windows.browser import SITE_NAMES

logger = logging.getLogger(__name__)

#: Favicon endpoint: serves PNG or ICO per domain (verified live 2026).
_DDG_ENDPOINT = "https://icons.duckduckgo.com/ip3/{domain}.ico"

_USER_AGENT = "Mozilla/5.0 Unscreen/0.5"
_FETCH_TIMEOUT_S = 5.0
_MAX_BYTES = 256 * 1024

#: Reverse of ``SITE_NAMES`` (domain -> display name): the site-bucket key
#: stores the lowercased display name (e.g. ``browser:youtube``), so this
#: maps it back to the domain the favicon service needs.
_DOMAIN_BY_NAME = {name.lower(): domain for domain, name in SITE_NAMES.items()}


def site_key_to_domain(app_key: str) -> str | None:
    """Map a site-bucket key (``browser:youtube``) to its domain.

    Returns ``None`` for the general ``browser`` bucket, for keys whose
    site is not a known normalized site, and for regular app keys.
    """
    if not isinstance(app_key, str):
        return None
    if not app_key.startswith("browser:"):
        return None
    return _DOMAIN_BY_NAME.get(app_key[len("browser:") :])


def is_site_bucket(app_key: str) -> bool:
    """Whether ``app_key`` is a normalized site bucket with an icon source."""
    return site_key_to_domain(app_key) is not None


# ---------------------------------------------------------------------------
# Android package icon extraction
# ---------------------------------------------------------------------------


def package_icon_png(package: str, size: int = 96) -> bytes | None:
    """PNG bytes for an Android package's launcher icon, or ``None``.

    Uses ``PackageManager.getApplicationIcon()`` via jnius to retrieve the
    app's ``Drawable``, renders it into an ARGB_8888 ``Bitmap``, and
    compresses to PNG. Returns ``None`` off-Android or on any failure so
    the caller can fall back to colored-initial avatars.
    """
    try:
        import threading

        from utils.android import get_activity

        logger.info(
            "package_icon_png: thread=%s package=%s",
            threading.current_thread().name,
            package,
        )
        activity = get_activity()
        if activity is None:
            logger.warning(
                "package_icon_png: get_activity() returned None for %s", package
            )
            return None

        from jnius import autoclass  # type: ignore

        Bitmap = autoclass("android.graphics.Bitmap")
        BitmapConfig = autoclass("android.graphics.Bitmap$Config")
        BitmapCompressFormat = autoclass("android.graphics.Bitmap$CompressFormat")
        Canvas = autoclass("android.graphics.Canvas")
        ByteArrayOutputStream = autoclass("java.io.ByteArrayOutputStream")

        pm = activity.getPackageManager()
        icon = pm.getApplicationIcon(package)
        bmp = Bitmap.createBitmap(size, size, BitmapConfig.ARGB_8888)
        canvas = Canvas(bmp)
        icon.setBounds(0, 0, size, size)
        icon.draw(canvas)
        out = ByteArrayOutputStream()
        bmp.compress(BitmapCompressFormat.PNG, 100, out)
        png = bytes(out.toByteArray())
        logger.info("package_icon_png: resolved %s (%d bytes)", package, len(png))
        return png
    except Exception:
        logger.warning("package_icon_png failed for %s", package, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Windows exe icon extraction
# ---------------------------------------------------------------------------

_icon_shell32 = None
_icon_user32 = None
_icon_gdi32 = None


def _ensure_win_dlls():
    """Lazy-load Windows DLLs (ctypes). No-op off-Windows."""
    global _icon_shell32, _icon_user32, _icon_gdi32
    if _icon_shell32 is not None:
        return True
    try:
        import ctypes

        _icon_shell32 = ctypes.windll.shell32
        _icon_user32 = ctypes.windll.user32
        _icon_gdi32 = ctypes.windll.gdi32
        return True
    except (AttributeError, OSError):
        return False


def exe_icon_png(exe_path: str, size: int = 48) -> bytes | None:
    """PNG bytes for a Windows executable's shell icon, or ``None``.

    Extracts the icon via ``ExtractIconExW``, renders it into a 32-bpp
    ``CreateDIBSection`` with ``DrawIconEx`` (alpha-aware), and encodes
    PNG with the pure-Python ``_png_from_rgba`` encoder. Falls back to
    ``SHGetFileInfoW`` when ``ExtractIconExW`` returns no handles.

    Returns ``None`` off-Windows, on missing files, or on any failure.
    """
    if not _ensure_win_dlls():
        return None
    if not isinstance(exe_path, str) or not exe_path.strip() or "\x00" in exe_path:
        return None
    try:
        import ctypes

        hicon = _extract_icon_handle(exe_path)
        if hicon is None:
            return None

        # Create a 32-bpp top-down DIB for direct pixel access.
        hdc = _icon_gdi32.CreateCompatibleDC(None)
        bmi = ctypes.create_string_buffer(40)
        ctypes.memset(bmi, 0, 40)
        struct.pack_into(
            "<IiiHHIIiiII", bmi, 0, 40, size, size, 1, 32, 0, 0, 0, 0, 0, 0
        )
        bits = ctypes.c_void_p()
        hbmp = _icon_gdi32.CreateDIBSection(hdc, bmi, 0, ctypes.byref(bits), None, 0)
        old = _icon_gdi32.SelectObject(hdc, hbmp)
        _icon_user32.DrawIconEx(hdc, 0, 0, hicon, size, size, 0, None, 3)  # DI_NORMAL
        _icon_gdi32.SelectObject(hdc, old)

        # Read BGRA pixel buffer, flip to top-down, encode as PNG.
        bgra = ctypes.string_at(bits, size * size * 4)
        png = _encode_bgra_png(bgra, size)

        _icon_gdi32.DeleteObject(hbmp)
        _icon_gdi32.DeleteDC(hdc)
        _icon_user32.DestroyIcon(hicon)
        logger.info("exe_icon_png: resolved %s (%d bytes)", exe_path, len(png))
        return png
    except Exception:
        logger.warning("exe_icon_png failed for %s", exe_path, exc_info=True)
        return None


def _extract_icon_handle(exe_path: str):
    """Return an HICON for *exe_path*, or ``None``.

    Tries ``ExtractIconExW`` first (best quality), then falls back to
    ``SHGetFileInfoW`` with ``SHGFI_ICON | SHGFI_SHELLICONSIZE``.
    """
    import ctypes
    from ctypes import wintypes

    # Primary: ExtractIconExW
    large = (ctypes.c_void_p * 1)()
    small = (ctypes.c_void_p * 1)()
    n = _icon_shell32.ExtractIconExW(exe_path, 0, large, small, 1)
    if n >= 1:
        return large[0] or small[0]

    # Fallback: SHGetFileInfoW
    try:

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ("hIcon", wintypes.HANDLE),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName", ctypes.c_wchar * 80),
            ]

        SHGFI_ICON = 0x000000100
        SHGFI_SHELLICONSIZE = 0x000000004
        SHGFI_USEFILEATTRIBUTES = 0x000000010
        FILE_ATTRIBUTE_NORMAL = 0x00000080

        info = SHFILEINFOW()
        ret = _icon_shell32.SHGetFileInfoW(
            exe_path,
            FILE_ATTRIBUTE_NORMAL,
            ctypes.byref(info),
            ctypes.sizeof(info),
            SHGFI_ICON | SHGFI_SHELLICONSIZE | SHGFI_USEFILEATTRIBUTES,
        )
        if ret and info.hIcon:
            return info.hIcon
    except Exception:
        logger.debug("SHGetFileInfoW fallback failed for %s", exe_path, exc_info=True)
    return None


def _encode_bgra_png(bgra: bytes, size: int) -> bytes:
    """Encode a raw BGRA pixel buffer (bottom-up) as a top-down PNG."""
    if size <= 0 or size > 4096:
        return b""
    rgba = bytearray(size * size * 4)
    for y in range(size):
        row_in = bgra[y * size * 4 : (y + 1) * size * 4]
        row_out = (size - 1 - y) * size * 4
        for x in range(size):
            b, g, r, a = row_in[x * 4 : x * 4 + 4]
            rgba[row_out + x * 4 : row_out + x * 4 + 4] = (r, g, b, a)
    return _png_from_rgba(bytes(rgba), size, size)


def fetch_site_favicon(app_key: str) -> bytes | None:
    """PNG bytes for a site bucket, or ``None`` when unavailable.

    ``None`` means "use the fallback": the caller keeps the colored-initial
    avatar. Network errors, unknown domains, and undecodable payloads all
    collapse to ``None`` so the dashboard never shows a broken image.
    """
    if not isinstance(app_key, str):
        return None
    domain = site_key_to_domain(app_key)
    if domain is None:
        return None
    try:
        request = urllib.request.Request(
            _DDG_ENDPOINT.format(domain=domain),
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_S) as response:
            data = response.read(_MAX_BYTES)
    except Exception:
        logger.debug("favicon fetch failed for %s", domain, exc_info=True)
        return None
    if data.startswith(b"\x89PNG"):
        return data if _valid_png(data) else None
    if data.startswith(b"\x00\x00\x01\x00"):
        return ico_to_png(data)
    logger.debug(
        "favicon response not PNG/ICO for %s (starts with %r)", domain, data[:4]
    )
    return None


def ico_to_png(ico: bytes) -> bytes | None:
    """Convert an ICO (PNG or 32-bpp DIB entries) to PNG bytes.

    Prefers an embedded PNG entry; otherwise renders the largest 32-bpp
    DIB entry. Returns ``None`` for anything else so callers fall back.
    """
    if len(ico) < 6 or ico[2:4] != b"\x01\x00":
        return None
    count = struct.unpack_from("<H", ico, 4)[0]
    entries: list[tuple[int, int, int, bytes]] = []
    for i in range(count):
        offset = 6 + 16 * i
        if offset + 16 > len(ico):
            return None
        w, h, _colors, _res, _planes, bpp, size, data_offset = struct.unpack_from(
            "<BBBBHHII", ico, offset
        )
        w = 256 if w == 0 else w
        h = 256 if h == 0 else h
        if data_offset + size > len(ico):
            continue
        entries.append((w, h, bpp, ico[data_offset : data_offset + size]))
    if not entries:
        return None
    for _w, _h, _bpp, data in entries:
        if data.startswith(b"\x89PNG"):
            return data if _valid_png(data) else None
    largest = max(entries, key=lambda entry: entry[0] * entry[1])
    _w, _h, bpp, data = largest
    if bpp in (4, 8, 32):
        return _dib_to_png(data)
    return None


def _dib_to_png(dib: bytes) -> bytes | None:
    """Render an ICO-flavored BITMAPINFOHEADER DIB (4/8/32 bpp) to PNG.

    In ICO files the DIB height is doubled by the trailing 1-bpp AND
    mask, so the color height is ``biHeight // 2`` and rows are
    bottom-up. Palette entries are 4 bytes (BGRx); the AND mask supplies
    transparency for 4/8-bpp entries (bit set = transparent), while
    32-bpp entries carry their own alpha. ``BI_RGB`` (0) and
    ``BI_BITFIELDS`` (3, identical 32-bpp pixel layout) are accepted.
    """
    if len(dib) < 40:
        return None
    (
        bi_size,
        bi_width,
        bi_height,
        bi_planes,
        bi_bpp,
        bi_comp,
        _size,
        _xppm,
        _yppm,
        bi_clr_used,
        _important,
    ) = struct.unpack_from("<IiiHHIIiiII", dib, 0)
    if bi_size < 40 or bi_planes != 1 or bi_bpp not in (4, 8, 32):
        return None
    if bi_comp not in (0, 3) or (bi_comp == 3 and bi_bpp != 32):
        return None
    if bi_width <= 0 or bi_width > 256 or bi_height == 0 or abs(bi_height) > 512:
        return None
    top_down = bi_height < 0
    height = -bi_height if top_down else bi_height // 2
    palette = _read_palette(dib, bi_size, bi_bpp, bi_clr_used)
    if palette is None:
        return None
    pixel_stride = _pixel_stride(bi_width, bi_bpp)
    data_offset = bi_size + (12 if bi_comp == 3 else 0) + len(palette)
    if data_offset + pixel_stride * height > len(dib):
        return None
    mask_stride = ((bi_width + 31) // 32) * 4
    mask_offset = data_offset + pixel_stride * height
    rgba = bytearray(b"\x00" * (bi_width * 4 * height))
    for y in range(height):
        row_start = data_offset + y * pixel_stride
        row = dib[row_start : row_start + pixel_stride]
        out_y = y if top_down else (height - 1 - y)
        for x in range(bi_width):
            color = _pixel_color(dib, row, x, bi_bpp, palette)
            if color is None:
                return None
            r, g, b = color
            if bi_bpp == 32:
                a = row[x * 4 + 3]
            else:
                mask_byte = dib[mask_offset + y * mask_stride + x // 8]
                a = 0 if (mask_byte >> (7 - x % 8)) & 1 else 255
            rgba[out_y * bi_width * 4 + x * 4 : out_y * bi_width * 4 + x * 4 + 4] = (
                r,
                g,
                b,
                a,
            )
    return _png_from_rgba(bytes(rgba), bi_width, height)


def _read_palette(
    dib: bytes, bi_size: int, bi_bpp: int, bi_clr_used: int
) -> bytes | None:
    """Palette bytes (BGRx entries) for 4/8-bpp DIBs; empty for 32-bpp."""
    if bi_bpp == 32:
        return b""
    count = bi_clr_used or (1 << bi_bpp)
    if bi_size + count * 4 > len(dib):
        return None
    return dib[bi_size : bi_size + count * 4]


def _pixel_stride(width: int, bi_bpp: int) -> int:
    if bi_bpp == 32:
        return width * 4
    if bi_bpp == 8:
        return (width + 3) & ~3
    return ((width + 1) // 2 + 3) & ~3


def _pixel_color(
    dib: bytes, row: bytes, x: int, bi_bpp: int, palette: bytes
) -> tuple[int, int, int] | None:
    if bi_bpp == 32:
        b, g, r, _a = row[x * 4 : x * 4 + 4]
        return r, g, b
    if bi_bpp == 8:
        index = row[x]
    elif x % 2 == 0:
        index = row[x // 2] >> 4
    else:
        index = row[x // 2] & 0x0F
    if index * 4 + 3 >= len(palette):
        return None
    b, g, r, _ = palette[index * 4 : index * 4 + 4]
    return r, g, b


def _png_from_rgba(rgba: bytes, width: int, height: int) -> bytes:
    """Encode RGBA8 rows (top-down, no filtering) as a minimal PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return b""
    raw = b"".join(
        b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _valid_png(data: bytes) -> bool:
    """Cheap structural check: PNG magic, chunk CRCs, inflatable IDAT."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    try:
        pos = 8
        idat = bytearray()
        while pos + 8 <= len(data):
            length = struct.unpack_from(">I", data, pos)[0]
            if pos + 12 + length > len(data):
                return False
            chunk = data[pos : pos + 12 + length]
            # CRC is the trailing 4 bytes of the chunk; it covers tag+data.
            if struct.unpack_from(">I", chunk, 8 + length)[0] != (
                zlib.crc32(chunk[4 : 8 + length]) & 0xFFFFFFFF
            ):
                return False
            if chunk[4:8] == b"IDAT":
                idat += chunk[8 : 8 + length]
            pos += 12 + length
        zlib.decompress(bytes(idat))
        return True
    except Exception:
        return False
