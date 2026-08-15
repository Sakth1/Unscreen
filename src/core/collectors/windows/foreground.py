import logging
import time

from core.collectors.windows.browser import analyze as analyze_browser
from core.collectors.windows.window import WindowAnalyzer
from core.config_manager import ConfigManager
from core.models import Tick, WatcherConfig
from core.storage import Storage

logger = logging.getLogger(__name__)


class ForegroundWatcher:
    def __init__(
        self,
        config: WatcherConfig | None = None,
        app_config: ConfigManager | None = None,
        storage: Storage | None = None,
    ):
        self.config = config or WatcherConfig(
            name="foreground",
            interval_s=2.0,
            enabled=True,
        )
        self._storage = storage
        self._url_processor = None
        self._url_extractor = None
        self._last_seen_url: str | None = None

        if app_config and app_config.url_extraction_enabled:
            try:
                from core.collectors.windows.url_extractor import UrlExtractor
                from core.url_processor import UrlProcessor

                self._url_extractor = UrlExtractor()
                self._url_processor = UrlProcessor()
                logger.info("URL extraction enabled")
            except Exception:
                logger.exception("Failed to initialize URL extractor")

    async def tick(self) -> Tick | None:
        window_data = WindowAnalyzer.analyze()
        if window_data is None:
            return None

        browser_info = analyze_browser(window_data["app"], window_data["title"])
        if browser_info is not None:
            window_data["browser"] = browser_info.browser

            if self._url_extractor and self._url_processor:
                result = self._url_extractor.extract(
                    browser_info.browser,
                    window_title=window_data.get("title"),
                    window_pid=window_data.get("pid"),
                )

                normalized = self._url_processor.normalize(
                    result.url,
                    method=result.method,
                    confidence=result.confidence,
                )

                if normalized.url is None:
                    title = browser_info.page_title
                    inferred = browser_info.inferred_domain if title else None
                    if inferred:
                        normalized = self._url_processor.normalize(
                            inferred, method=None, confidence="low"
                        )

                if normalized.url:
                    window_data["url"] = normalized.url
                    if normalized.url != self._last_seen_url:
                        self._last_seen_url = normalized.url
                        if self._storage:
                            self._storage.write_url_visit(
                                url=normalized.url,
                                seen_at=window_data.get("timestamp", time.time()),
                                extraction_method=normalized.extraction_method,
                                confidence=normalized.confidence,
                                scheme=normalized.scheme,
                                host=normalized.host,
                                domain=normalized.domain,
                                path=normalized.path,
                                is_trackable=normalized.is_trackable,
                            )
                else:
                    window_data["page_title"] = browser_info.page_title
                    if browser_info.inferred_domain:
                        window_data["inferred_domain"] = browser_info.inferred_domain
            else:
                window_data["page_title"] = browser_info.page_title
                if browser_info.inferred_domain:
                    window_data["inferred_domain"] = browser_info.inferred_domain

        return Tick(
            watcher="foreground",
            data=window_data,
        )
