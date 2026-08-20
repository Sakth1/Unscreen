# App icons in the dashboard (Windows + Android)

Date: 2026-08-20

## 1. Executive summary

- Windows icons: extract from the app's **executable path** via `ExtractIconExW` / `SHGetFileInfoW` (ctypes), render the `HICON` into a 32-bit `CreateDIBSection` with `DrawIconEx`, read the RGBA pixels, and encode PNG with a ~40-line pure-Python encoder (zlib + struct). No Pillow needed (it is only in the `e2e` extra, not production deps). PyWin32 is an optional alternative — `pywinauto` already pulls it in on Windows.
- Gap in the current collector: `WindowAnalyzer` stores only the **process name** (e.g. `chrome.exe`), never the exe path. Icon extraction requires the path; the recommended fix is to capture `psutil.Process(pid).exe()` at collection time and store it in the payload (app_key stays the process name).
- Android icons: `PackageManager.getApplicationIcon(pkg)` / `PackageItemInfo.loadIcon(pm)` returns a `Drawable`; draw it into a `Bitmap` (ARGB_8888) via `Canvas`, then `Bitmap.compress(PNG)` — all reachable from Python through the existing jnius pattern (`get_activity()` in `src/utils/android.py`, `package_resolver.py`).
- flet 0.86.5 accepts PNG **bytes directly**: `Image.src: Union[str, bytes]` (URL / asset path / base64 string / raw bytes) and `CircleAvatar.foreground_image_src: Union[str, bytes]` with fallback chain `foreground_image_src → background_image_src → bgcolor`. There is **no `src_base64` parameter** in 0.86 — base64/bytes go straight into `src`. Pass raw PNG bytes; no asset bundling needed on any platform.
- UWP/Store apps: the "proper" WinRT route (`Windows.Management.Deployment.PackageManager.FindPackagesForUser`, `AppListEntry.AppInfo.DisplayInfo.GetLogo(Size)`) requires the third-party `winrt`/`winsdk` Python package and is not worth it. UWP executables in `WindowsApps` are directly readable, so the exe-path + `SHGetFileInfoW` path covers them too.
- The Explorer icon cache (`%LocalAppData%\Microsoft\Windows\Explorer\iconcache_*.db`) is an undocumented, forensics-only format — do not use it.
- Site buckets (`browser:youtube`, …): use the favicon endpoint `https://icons.duckduckgo.com/ip3/<domain>.ico`, verified live in 2026 (200 `image/x-icon`/`image/png`; 404 on unknown domains, so fall back to the browser's icon, then to initials).
- Cache everything in a new SQLite `app_icons` table (repo is already SQLite-based, `SCHEMA_VERSION = 8` in `src/core/storage/__init__.py`); resolve lazily in a background thread via the existing `run_task` pattern in `top_apps_card.py`, keeping the colored-initials `CircleAvatar` as instant placeholder.

## 2. Windows icon extraction

### 2.1 Primary APIs (verified against Microsoft Learn)

| API | Role |
| --- | --- |
| [`SHGetFileInfoW`](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shgetfileinfow) | Shell-resolved icon for a file path; `SHGFI_ICON \| SHGFI_SHELLICONSIZE \| SHGFI_USEFILEATTRIBUTES` returns the shell's preferred-size icon handle in the [`SHFILEINFOW`](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shfileinfow) struct (`hIcon`, `iIcon`, `dwAttributes`, `szDisplayName[260]`, `szTypeName[80]`). |
| [`ExtractIconExW`](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-extracticonexw) | Extracts arrays of large/small icon handles directly from an .exe/.dll/.ico; pass `index=0, numIcons=1`; every returned handle must be freed with `DestroyIcon`. |
| [`GetIconInfo`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-geticoninfo) | Alternative access to icon pixels via `hbmColor`/`hbmMask` (only needed if not using `CreateDIBSection`). |
| [`GetDIBits`](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getdibits) | Copies bitmap bits into a DIB buffer (only needed if not using `CreateDIBSection`). |
| [`DrawIconEx`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawiconex) | Draws an icon into a DC, alpha-aware with `DI_NORMAL`, with arbitrary target size. |
| [`CreateDIBSection`](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createdibsection) | Allocates a 32-bpp top-down bitmap and returns a **direct pointer to the pixel buffer** — no `GetDIBits` round-trip needed. |

PyWin32 equivalents (verified at [mhammond.github.io/pywin32](https://mhammond.github.io/pywin32/)): [`win32gui.ExtractIconEx(moduleName, index, numIcons=1)`](https://mhammond.github.io/pywin32/win32gui__ExtractIconEx_meth.html) returns two lists of handles (must `DestroyIcon`), [`win32gui.GetIconInfo`](https://mhammond.github.io/pywin32/win32gui__GetIconInfo_meth.html) returns `(fIcon, xHotspot, yHotspot, hbmMask, hbmColor)`, and [`PyCBitmap.GetBitmapBits`](https://mhammond.github.io/pywin32/PyCBitmap.html) reads pixels. **`win32ui.CreateIconFromHandle` does not exist** in current PyWin32 (the docs page 404s); the drawing path is `CreateDCFromHandle` + `CreateBitmap` + `CreateCompatibleDC` + `SelectObject` + `DrawIcon` + `GetBitmapBits(True)`.

### 2.2 What the collector currently knows

- `src/core/collectors/windows/window.py` uses ctypes `GetForegroundWindow` → `GetWindowTextW` → `GetWindowThreadProcessId` → `psutil.Process(pid).name()`; only the process name (e.g. `chrome.exe`) becomes the `app_key`. No exe path is persisted.
- Site buckets come from `src/core/collectors/windows/browser.py` (`BROWSER_PROCESSES`, `SITE_NAMES`, `DOMAIN_KEYWORDS`); browser rows (`browser:youtube`, …) can use a favicon instead of an exe icon.
- The repository stores events/sessions in SQLite (`SCHEMA_VERSION = 8`, `Storage` in `src/core/storage/__init__.py`, db at `get_data_dir()/data.db` — `%APPDATA%\Unscreen` on Windows per `src/utils/paths.py`).

### 2.3 Recommended extraction recipe (pure ctypes, zero new deps)

```python
import ctypes, zlib, struct
from ctypes import wintypes

shell32 = ctypes.windll.shell32
user32  = ctypes.windll.user32
gdi32   = ctypes.windll.gdi32

class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HANDLE),
                ("hbmColor", wintypes.HANDLE)]

def icon_to_png(exe_path: str, size: int = 48) -> bytes | None:
    large, small = (ctypes.c_void_p * 1)(), (ctypes.c_void_p * 1)()
    n = shell32.ExtractIconExW(exe_path, 0, large, small, 1)
    if n < 1:
        return None
    hicon = large[0] or small[0]

    hdc = gdi32.CreateCompatibleDC(None)                 # memory DC
    bmi = ctypes.create_string_buffer(40)                # BITMAPINFOHEADER, 32bpp
    ctypes.memset(bmi, 0, 40)
    struct.pack_into("IIiiIIIIII", bmi, 0, 40, size, size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc, bmi, 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(hdc, hbmp)
    user32.DrawIconEx(hdc, 0, 0, hicon, size, size, 0, None, 3)   # DI_NORMAL = 3
    gdi32.SelectObject(hdc, old)
    png = _encode_png(ctypes.string_at(bits, size * size * 4), size)  # BGRA rows
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc)
    user32.DestroyIcon(hicon)
    return png

def _encode_png(bgra: bytes, size: int) -> bytes:        # pure-Python PNG (RGBA8)
    rgba = bytearray(size * size * 4)
    for y in range(size):                                # flip to top-down, BGRA->RGBA
        row_in  = bgra[y * size * 4 : (y + 1) * size * 4]
        row_out = (size - 1 - y) * size * 4
        for x in range(size):
            rgba[row_out + x*4:row_out + x*4+4] = row_in[x*4+2:x*4+3] + row_in[x*4+1:x*4+2] + row_in[x*4:x*4+1] + row_in[x*4+3:x*4+4]
    raw = b"".join(b"\x00" + rgba[i:i+size*4] for i in range(0, len(rgba), size*4))
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
```

Notes:
- `CreateDIBSection` with a 32-bpp top-down header gives **direct access** to the RGBA pixel buffer — no `GetDIBits` call needed. `DrawIconEx(..., DI_NORMAL)` renders alpha correctly.
- Fallback ordering for a missing exe: `SHGetFileInfoW` with `SHGFI_ICON|SHGFI_SHELLICONSIZE|SHGFI_USEFILEATTRIBUTES` (works for paths that may not exist yet), then initials.
- The hand-rolled PNG encoder is ~40 lines; validate round-trip in e2e tests with Pillow, which is available in the `e2e` extra (`pyproject.toml`).
- PyWin32 alternative: `win32gui.ExtractIconEx` + `win32ui.CreateDCFromHandle` + `PyCBitmap.GetBitmapBits` (already present transitively via `pywinauto`), but the ctypes path keeps the dependency story unchanged.

### 2.4 UWP / Store apps

- The WinRT enumeration path exists and is documented: [`PackageManager.FindPackagesForUser(String.Empty)`](https://learn.microsoft.com/en-us/uwp/api/windows.management.deployment.packagemanager.findpackagesforuser) (no admin needed for the current user; `packageQuery` capability required in desktop apps), then [`Package.GetAppListEntriesAsync()`](https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.package), [`AppListEntry.AppInfo`](https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.core.applistentry.appinfo), and [`AppDisplayInfo.GetLogo(Size)`](https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.appdisplayinfo.getlogo) → `RandomAccessStreamReference` ("the largest logo in your Package.appxmanifest file that will fit in the specified Size"). [`Package.Logo`](https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.package) also exposes the logo directly.
- Problem: all of these are WinRT COM projections. Pure ctypes can't call them; it needs the third-party `winrt-Windows.Management.Deployment` / `winsdk` pip packages (Windows-only, extra build consideration for Flet packaging).
- Pragmatic conclusion: UWP app processes have their own executables under `C:\Program Files\WindowsApps\*` (e.g. `Calculator.exe`), which are **directly readable**; the exe-path + `SHGetFileInfoW`/`ExtractIconExW` route returns the correct icon for them without WinRT. Revisit WinRT only if exe-path extraction ever proves insufficient.

### 2.5 The Explorer icon cache is not usable

`%LocalAppData%\Microsoft\Windows\Explorer\iconcache_*.db` is a proprietary binary format (version strings 0x0506/0x0507) documented only in forensics write-ups (e.g. thinkdfir.com "Windows 10 Explorer IconCache db" analysis); it is not a supported API, changes between Windows versions, and cannot be read reliably. Do not use it.

## 3. Android icon extraction

### 3.1 Primary APIs (verified)

- [`PackageManager.getApplicationIcon(String)`](https://developer.android.com/reference/android/content/pm/PackageManager) — "Retrieve the icon associated with an application" (returns `Drawable`). (Direct page fetch timed out during research; verified via search snippets and the `MockPackageManager` reference.)
- [`PackageItemInfo.loadIcon(PackageManager)`](https://developer.android.com/reference/android/content/pm/PackageItemInfo) — API level 1; "Retrieve the current graphical icon associated with this item… If the item does not have an icon, the item's default icon is returned" — same machinery `ApplicationInfo` inherits (the repo already calls `loadLabel` on `ApplicationInfo` in `src/core/collectors/android/package_resolver.py`).
- [`AdaptiveIconDrawable`](https://developer.android.com/reference/android/graphics/drawable/AdaptiveIconDrawable) (API 26+) — icons are two layers (background/foreground) sized 108×108 dp with a 72×72 dp safe zone; the launcher applies the device mask (circle/squircle). `getApplicationIcon` returns the maskless layered drawable; drawing it into a bitmap yields a square icon — which is exactly right for flet's `CircleAvatar`, which applies its own circular clip.
- Drawable→Bitmap: `Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)` + `Canvas(bitmap)` + `drawable.setBounds(0, 0, w, h)` + `drawable.draw(canvas)` + `bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)` (cross-verified via [msol.io drawable→bitmap recipe](https://msol.io/blog/tech/convert-a-drawable-to-a-bitmap/) and Stack Overflow; the primary `Bitmap` reference page also timed out on direct fetch).
- Density baseline for choosing px size: [Android screen densities](https://developer.android.com/training/multiscreen/screendensities) — mdpi 48 px, hdpi 72 (1.5×), xhdpi 96 (2×), xxhdpi 144 (3×), xxxhdpi 192 (4×); launcher icons live in `mipmap-*` and may scale up 25%.
- Chaquopy 17.0 ([Android platform docs](https://chaquo.com/chaquopy/doc/current/android.html)): `Python.start(new AndroidPlatform(context))` requires `context` to be an Activity/Service/Application; Python ↔ Java interop via `jnius.autoclass`.

### 3.2 How it plugs into this repo

The interop already exists:
- `src/utils/android.py` — `get_activity()`: reads `MAIN_ACTIVITY_HOST_CLASS_NAME` (set by the Flet runtime on Android), `autoclass(activity_host_class).mActivity`, cached; returns `None` off-Android.
- `src/core/collectors/android/package_resolver.py` — already does `pm.getApplicationInfo(pkg, 0)` then `info.loadLabel(pm)`; adding `icon = info.loadIcon(pm)` is one line.
- `src/core/collectors/android/usage_stats.py` — the `jnius.autoclass` pattern to mirror.

```python
from jnius import autoclass
from utils.android import get_activity

def package_icon_png(package: str, size: int = 96) -> bytes | None:
    activity = get_activity()
    if activity is None:
        return None
    Bitmap, Canvas, Rect = (autoclass(n) for n in
        ("android.graphics.Bitmap", "android.graphics.Canvas", "android.graphics.Rect"))
    ByteArrayOutputStream = autoclass("java.io.ByteArrayOutputStream")
    pm = activity.getPackageManager()
    icon = pm.getApplicationIcon(package)          # Drawable (or loadIcon via ApplicationInfo)
    bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
    canvas = Canvas(bmp)
    icon.setBounds(0, 0, size, size)
    icon.draw(canvas)
    out = ByteArrayOutputStream()
    bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
    return bytes(out.toByteArray())
```

Choose `size` = 96 px (crisp on up to 3× density displays; PNG stays ~2–6 KB). Adaptive icons render square; `CircleAvatar` clips circularly.

## 4. Getting icons into flet

Verified against the installed flet 0.86.5 source (`.venv/Lib/site-packages/flet/controls/core/image.py`, `flet/controls/material/circle_avatar.py`) and the flet docs ([Image](https://flet.dev/docs/controls/image), [CircleAvatar](https://flet.dev/docs/controls/circleavatar)):

- `ft.Image(src: Union[str, bytes])` — "It can be one of the following: A URL or local asset file path; A base64 string; Raw bytes." `placeholder_src` accepts the same.
- `ft.CircleAvatar(foreground_image_src: Union[str, bytes], background_image_src=..., bgcolor=...)` — "If `foreground_image_src` fails then `background_image_src` is used…", with `bgcolor` as the final fallback; `on_image_error` fires on load failure.
- **There is no `src_base64` parameter in flet 0.86** — base64 strings and raw bytes go directly into `src`/`foreground_image_src`.

Recommended: pass the **raw PNG bytes** straight to the control — no files on disk, no asset bundling, identical code on desktop and Android:

```python
ft.CircleAvatar(
    radius=18,
    foreground_image_src=png_bytes,   # resolved in background; None while loading
    bgcolor=color,                    # existing initials color stays as instant placeholder
)
```

Integration points:
- `src/UI/components/top_apps_card.py` already renders each app row with a colored `CircleAvatar` holding initials and already has the async pattern to reuse (`_open_all_apps` / `run_task`) — resolve icons in a background thread, update the avatar when bytes arrive; on failure keep initials.
- Browser/site buckets (`browser:youtube`, …) get `https://icons.duckduckgo.com/ip3/<domain>.ico` where `<domain>` comes from the existing `SITE_NAMES`/`DOMAIN_KEYWORDS` mapping in `src/core/collectors/windows/browser.py`; fall back to the browser's exe icon, then initials.
- Request small icons (Windows 48 px, Android 96 px) so the UI payload stays small; flet base64-encodes bytes for transport.

## 5. Recommended design

Single architecture, both platforms, no new runtime dependencies:

1. **Collection time (Windows only change):** `WindowAnalyzer` (`src/core/collectors/windows/window.py`) additionally records `psutil.Process(pid).exe()` in the payload; `app_key` remains the process name. This closes the only real gap.
2. **Resolver module** (new, e.g. `src/core/icons/icon_resolver.py`), platform-dispatched like the existing collectors:
   - Windows: `exe_path → HICON (ExtractIconExW, fallback SHGetFileInfoW) → DrawIconEx into CreateDIBSection → RGBA → pure-Python PNG (48 px)`.
   - Android: `package → loadIcon/loadBitmap → Bitmap → Bitmap.compress(PNG) (96 px)` via jnius.
   - Site buckets: DDG favicon endpoint (verified live 2026), then browser exe icon, then initials.
3. **SQLite cache** — new `app_icons` table (schema v9 migration of `SCHEMA_VERSION = 8`): `app_key TEXT PRIMARY KEY, source TEXT, fingerprint TEXT, png BLOB, width INT, updated_at INT`. Fingerprints: Windows `sha256(exe_path | mtime_ns)`; Android `package` (+ `versionCode`/`lastUpdateTime` check on refresh); sites `sha256(domain)`.
4. **UI:** `TopAppsCard` resolves the icons for the currently visible range via `run_task` in a background thread; `CircleAvatar.foreground_image_src = png_bytes` with existing initials + `bgcolor` as instant placeholder and `on_image_error`/`None` fallback. Old cache entries are evicted when their fingerprint changes (app update/move).
5. **Testing:** unit tests for the PNG encoder + cache (Pillow available in the `e2e` extra for round-trip validation); e2e visual check that avatars render icons; on non-Windows/non-Android environments the resolver returns `None` and the UI degrades to initials.

## 6. Open questions / risks

- **Payload/schema change:** storing exe paths at collection time grows events and changes the payload shape; historical events contain process names only (fall back to initials or lazy `psutil` name→path lookup for them).
- **Android adaptive-icon masking:** `loadIcon` returns the unmasked layered drawable; rendering square + `CircleAvatar` clip is the intended look, but OEM launcher masks are not reproduced — acceptable for in-app avatars.
- **Windows icon quality:** `ExtractIconExW` index 0 can return small/legacy VGA icons for some apps; `SHGetFileInfoW`+`SHGFI_SHELLICONSIZE` is the better first choice for quality, `DrawIconEx` upscaling can look soft — verify on a sample of apps.
- **DDG favicon service** is third-party and offline-prone; requests also leak the domain to DDG. Mitigate: cache aggressively, fall back gracefully, and note privacy trade-off in the design.
- **Hand-rolled PNG encoder** correctness (CRC, row filters); validate with Pillow in e2e tests before shipping.
- **PyWin32 vs ctypes:** ctypes keeps deps unchanged; pywin32 is already transitively present via `pywinauto` if the team prefers the higher-level API.
- **WinRT route** remains a documented-but-deferred option if UWP exe-path extraction ever fails for specific Store apps (would add `winrt`/`winsdk` Windows-only deps).