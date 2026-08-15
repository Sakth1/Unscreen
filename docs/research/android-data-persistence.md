# Android data persistence across uninstall

Date: 2026-08-15

## Summary

Unscreen now keeps its SQLite database across an Android app uninstall in two
complementary ways, mirroring the Windows contract ("data survives by
default, the user may delete it") as closely as Android's security model
allows:

1. **Android Auto Backup (cloud)** — enabled explicitly via
   `manifest_application = { allowBackup = "true" }` in
   [`pyproject.toml`](../../pyproject.toml). The OS uploads app-internal
   data (including the database under `files/`) to the user's Google Drive
   and restores it automatically on reinstall.
2. **Durable copy in user-visible shared storage (on-device)** — a
   consistent snapshot of `data.db` is written to the MediaStore Downloads
   collection at `Download/Unscreen-data-backup/unscreen.db` (API 29+,
   no permissions). The copy survives an uninstall on the device itself and
   stays visible to and deletable by the user.

Implementation: [`src/core/storage/android_durable.py`](../../src/core/storage/android_durable.py),
decision record ADR-0003.

## How other apps restore data after reinstall

The dominant mechanism is **Android Auto Backup** (`android:allowBackup`,
on by default for apps targeting API 23+):

- Backs up the app's internal storage (files, databases, shared prefs) to
  the user's Google Drive; restored automatically on reinstall on the same
  or a new device with the same Google account.
- Limits: **25 MB file-based data per app** (Unscreen's DB is far below
  this), backups run opportunistically (device idle + charging + Wi-Fi,
  at most once per 24 h — a fresh install may have no backup yet if the
  user uninstalls within hours).
- Apps that need data to survive on the device itself use **shared
  storage** (MediaStore / Storage Access Framework), because internal
  storage, `Android/data/` and `Android/media/<package>` are wiped on
  uninstall.

## Verified platform facts

- Our manifest previously had no `allowBackup` attribute → default `true`:
  Auto Backup was already active for `files/data/data.db` (default backup
  domains include `files/`). It is now explicit and logged.
- `[tool.flet.android].manifest_application` (flet 0.86.5 build template)
  renders extra attributes on the `<application>` element — confirmed by
  inspecting the cached template
  (`~/.flet/cache/build-template/v0.86.5/flet-build-template.zip`). It
  cannot reference `res/xml` resources, so `dataExtractionRules` /
  `fullBackupContent` would require a vendored custom `--template`; not
  needed for the default include-everything rules.
- flet's Android runtime (`com.flet.serious_python_android.PythonActivity`)
  is **only a static `mActivity` holder** — no `onActivityResult` /
  `onRequestPermissionsResult` dispatch, and the generated `MainActivity`
  is a bare `FlutterActivity`. Consequently the **Storage Access
  Framework folder picker is not reachable from Python** today (verified
  against flet-dev/serious-python source). Runtime permission dialogs have
  the same limitation.
- MediaStore (API 29+): apps may contribute files to `Downloads` with **no
  permissions** and read/update their own contributions while installed.
  Attribution is tracked per app; **after an uninstall + reinstall the
  attribution is lost** — the file survives, but reading it back silently
  is not allowed (documented on
  developer.android.com/training/data-storage/shared/media). The user can
  still copy it back manually from a file manager.
- pyjnius can drive all of this synchronously (no callbacks): `ContentResolver`
  insert/query/update + `openOutputStream`/`openInputStream` streaming —
  the same bridging pattern as the existing APK-install flow in
  `src/core/update_checker.py`.

## Design

`AndroidDurableBackup` (`src/core/storage/android_durable.py`):

- Gate: `Build.VERSION.SDK_INT >= 29` (MediaStore Downloads does not exist
  below API 29; older devices rely on Auto Backup only).
- Snapshot: `VACUUM INTO` a temp file on a read-only connection (a
  consistent point-in-time copy), falling back to a raw file copy on
  failure. Skipped entirely when the DB file is absent or empty.
- Upload: `ContentResolver.insert` into
  `MediaStore.Downloads.EXTERNAL_CONTENT_URI` with
  `RELATIVE_PATH = Download/Unscreen-data-backup/`,
  `DISPLAY_NAME = unscreen.db`, `MIME_TYPE = application/octet-stream`,
  `IS_PENDING = 1` → stream bytes via `openOutputStream` → clear
  `IS_PENDING`. Existing rows (same install) are updated in place instead
  of re-inserted.
- Sync triggers: on collection stop (`force=True`) and hourly in the health
  monitor; throttled to one sync per 60 s otherwise.
- Restore: when a data dir is created fresh on Android, a present backup
  row is streamed back before the DB is initialized (same-install restores,
  e.g. after a manual data reset; after a real reinstall the attribution is
  lost and the file is not readable without SAF).
- Failure policy: every failure is logged and swallowed — collection never
  breaks because the backup copy failed.

## Limitations (documented, not hidden)

- Auto Backup is cloud restore: not instant, requires the Google account
  and the device backup setting, and a freshly installed app may not have a
  backup yet.
- On API 33+ the Downloads copy cannot be read back programmatically after
  a reinstall (Android attribution model); the user can copy it back
  manually, and a SAF-based restore flow is future work once flet exposes
  activity-result callbacks.
- On API ≤ 28 there is no Downloads copy (MediaStore Downloads requires
  API 29); Auto Backup still covers those devices.

## Device verification checklist

- `adb shell bmgr backupnow com.mycompany.unscreen` → `Backup finished` (or
  `d2d` reason) confirms Auto Backup picks up the DB.
- Install a dev APK, let collection run, check the copy exists:
  `adb shell ls /sdcard/Download/Unscreen-data-backup/` and
  `adb shell content query --uri content://media/external_primary/downloads`
- Uninstall, reinstall the same APK, verify: (a) cloud restore on next
  launch when backups are enabled (device may restore on install), or (b)
  the `Download/Unscreen-data-backup/unscreen.db` file still exists and can
  be copied back manually.
- Startup log line `Storage initialized: ... android_durable_backup=Download/Unscreen-data-backup/unscreen.db`
  confirms the durable backup is armed.
