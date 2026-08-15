# ADR-0003: Android Durable Data Backup

**Status:** Accepted (v0.4.10-dev3)

## Context

Unscreen's contract on Windows is "data survives by default, the user may
delete it" (`%APPDATA%\Unscreen`). On Android, the database lives in app
internal storage (`files/data/data.db`), which is wiped on uninstall.
Losing the whole history when an Android user removes the app breaks the
Windows parity contract, and Android's scoped-storage rules block the
obvious fixes:

- External shared directories (like `Download/`) are **not** freely
  writable by path anymore (Scoped Storage, API 29+).
- The Storage Access Framework folder picker — the sanctioned alternative —
  is **not reachable from Python** in the flet runtime: the generated
  `MainActivity` is a bare `FlutterActivity` and
  `com.flet.serious_python_android.PythonActivity` only exposes a static
  `mActivity` holder with no activity-result dispatch (verified against
  flet-dev/serious-python source, 2026-08).
- MediaStore contributions lose app attribution after an uninstall +
  reinstall, so a reinstall cannot silently re-import them.

## Decision

Pursue **two complementary, permissionless mechanisms** (user-approved
scope "Auto Backup + Downloads copy"):

1. **Android Auto Backup, made explicit.** Declare
   `manifest_application = { allowBackup = "true" }` (already the platform
   default for our targetSdk, now explicit and logged). The OS backs up
   internal data — including the SQLite DB under `files/` — to the user's
   Google Drive and restores it on reinstall. No code; reliable; invisible
   to the user.
2. **Durable snapshot in user-visible shared storage.** A new
   `AndroidDurableBackup` component (`src/core/storage/android_durable.py`)
   writes a consistent `VACUUM INTO` snapshot of `data.db` to the MediaStore
   Downloads collection at `Download/Unscreen-data-backup/unscreen.db`
   (API 29+, no permissions, user-deletable, survives uninstall on the
   device). The snapshot is streamed through `ContentResolver`
   insert/update + `openOutputStream` via pyjnius — no callbacks needed,
   same bridging pattern as `update_checker.py`.

### Component behavior

- **Sync triggers:** on collection stop (`force=True`) and hourly in the
  health-monitor loop; throttled to ≥ 60 s between non-forced syncs.
- **Restore:** on a fresh Android data dir, an existing snapshot row
  (same install) is streamed back before DB init; after a true reinstall
  attribution is lost and the file is not programmatically readable — the
  user can copy it back manually from a file manager.
- **Failure policy:** every error is logged and swallowed; collection and
  normal DB writes never depend on the backup path succeeding.

## Consequences

Positive:

- History survives uninstall on the same device and (via Auto Backup) the
  same Google account, with zero permissions and zero dialogs.
- The Downloads copy is visible and deletable — the user can always free
  the space or remove the data, honoring the Windows parity contract.
- No custom build template needed for the manifest change (flet renders
  `manifest_application` attributes; only `res/xml` references would need
  a vendored template, which we do not use).

Negative / accepted:

- Auto Backup is not instant and may not exist for a freshly installed app
  (OS runs backups at most once per 24 h, idle/charging/Wi-Fi).
- On API 33+ the Downloads copy cannot be re-imported automatically after
  reinstall; manual copy-back is the fallback. A SAF folder-picker restore
  remains future work once flet exposes activity-result callbacks.
- On API ≤ 28 there is no Downloads copy at all (MediaStore Downloads
  requires API 29); Auto Backup still applies.

Unchanged: the DB stays in WAL mode and internal storage; the snapshot is
only ever read via a separate read-only connection.

## Alternatives considered

- **SAF folder picker** (ACTION_OPEN_DOCUMENT_TREE): blocked — no
  activity-result dispatch in the flet Python runtime today.
- **Write to public Download/ by path on API ≤ 28**: legacy path only;
  excluded to keep one code path (MediaStore, API 29+).
- **Room/dataExtractionRules/fullBackupContent XML**: would require a
  vendored flet build template; default rules already cover `files/`.
- **ADB/`run-as` or root**: not acceptable for a normal user.
- **Cloud sync (DroidDB-style / WebDAV)**: out of scope for this version;
  documented in `docs/research/android-data-persistence.md` as future work.
