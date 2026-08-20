"""Analytics read layer: derived per-app totals from app_sessions.

AnalyticsStore is the app's analytics engine (ADR-0004). It runs on the
same SQLite store as everything else — no DuckDB — so it works on every
platform, including the Android APK where binary wheels are unavailable.
"""

from core.analytics.analytics_store import ALL_DEVICES, AnalyticsStore, AppTotal

__all__ = ["ALL_DEVICES", "AnalyticsStore", "AppTotal"]
