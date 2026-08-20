# ADR-0004: Analytics on SQLite, Not DuckDB

**Status:** Accepted (v0.6.0)

## Context

Issue #26 (per-app daily and weekly totals) and the rest of the v0.6.0
milestone (#27–#30) were originally titled "…(DuckDB)", reflecting a plan
to make DuckDB the analytics engine. Two things changed since that plan
was written:

1. **The v0.4.0 reset.** The pre-0.4.0 codebase shipped a DuckDB
   `AnalyticsStore`; v0.4.0 deliberately removed it — "DuckDB removed:
   AnalyticsStore, sync loop, DbViewer toggle, config property, `duckdb`
   dependency all stripped — simplified DB stack to SQLite-only"
   (dev_dairy). Issue #26's query text (`FROM events WHERE watcher=…`)
   targets that removed, pre-v8 schema.
2. **The Android packaging constraint.** The Android build is a flet APK
   packaged by Serious Python, which installs **binary wheels only**.
   `lz4` already broke the APK once for exactly this reason
   (docs/research/android-lz4-flet-build.md). DuckDB publishes **no
   Android wheels** and is not in Flet's built-in binary package list, so
   an unconditional `duckdb` dependency breaks the APK build. A
   Windows-gated dependency would leave Android — half the product —
   without the analytics engine, forcing two implementations behind one
   interface.

At the data volumes Unscreen collects (tens of thousands of rows), the
queries in #26–#30 are plain `GROUP BY`s that SQLite answers in
microseconds. DuckDB's columnar strengths are irrelevant at this scale,
and the query in #26 ports 1:1 to `app_sessions` + SQLite.

## Decision

**Build the analytics layer on SQLite — the app's existing storage
engine — as a dedicated read-side module (`core/analytics`).** Do not
re-add DuckDB. The issue/milestone titles are updated to drop the
"(DuckDB)" framing.

Consequences:

- `AnalyticsStore` reads the derived `app_sessions` table directly on the
  live WAL database — no snapshot, export, or sync pipeline between
  engines.
- The engine works identically on Windows and Android (no new dependency
  enters the Android APK).
- The store's public surface (`AppTotal` rows with `app_key`, `app_name`,
  `total_s`, `share_pct`) is engine-agnostic by design: if a future
  milestone genuinely needs DuckDB (e.g. #30 pre-computed summary tables
  at scale), it can be introduced behind the same interface without
  touching callers.
- Dead pre-analytics helpers (`Storage.get_today_seconds`,
  `Storage.get_today_top_apps`) are removed; they were unused, used UTC
  midnight instead of the local day, and skipped device scoping.

## Alternatives Considered

1. **DuckDB on Windows + SQLite fallback on Android.** Honors the original
   title but means two engines, two test matrices, and a platform split in
   behaviour — all for queries SQLite already answers quickly. Rejected.
2. **DuckDB everywhere.** Simplest code, but breaks the Android APK build
   (no Android wheel). Rejected.
3. **Keep the old `get_today_*` Storage helpers and extend them.** They
   live in the low-level storage layer, which should own data access, not
   derived analytics; they were also buggy (UTC day, no device scope).
   Replaced by `AnalyticsStore`.