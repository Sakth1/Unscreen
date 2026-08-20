"""Site-bucket favicon resolution with graceful fallback (F9c).

Browser site buckets (``browser:youtube``, ``browser:github``, ...) get a
real icon where possible. Fallback chain for a site row:

    DDG favicon -> None (the colored-initial avatar stays)

The general ``Browser`` bucket (``app_key == "browser"``) has no favicon;
it keeps the colored-initial avatar until the Windows exe-icon stage
lands (see docs/app-icons-research.md).

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
    if not app_key.startswith("browser:"):
        return None
    return _DOMAIN_BY_NAME.get(app_key[len("browser:") :])


def is_site_bucket(app_key: str) -> bool:
    """Whether ``app_key`` is a normalized site bucket with an icon source."""
    return site_key_to_domain(app_key) is not None


def fetch_site_favicon(app_key: str) -> bytes | None:
    """PNG bytes for a site bucket, or ``None`` when unavailable.

    ``None`` means "use the fallback": the caller keeps the colored-initial
    avatar. Network errors, unknown domains, and undecodable payloads all
    collapse to ``None`` so the dashboard never shows a broken image.
    """
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
