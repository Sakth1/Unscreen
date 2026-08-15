import datetime
import logging
import time

logger = logging.getLogger(__name__)

_MS_PER_S = 1000


def get_current_time_ms() -> int:
    return int(time.time() * 1000)


def day_start_ms(now_ms: int) -> int:
    try:
        local_dt = datetime.datetime.fromtimestamp(now_ms / _MS_PER_S)
        local_midnight = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(local_midnight.timestamp() * _MS_PER_S)
    except (OSError, OverflowError, ValueError):
        logger.debug("Failed to compute day start for %d, defaulting to now", now_ms)
        return now_ms


def fmt_timestamp(ts: int) -> str:
    """Format a Unix epoch milliseconds timestamp in local time with tz offset."""
    try:
        local = datetime.datetime.fromtimestamp(ts / _MS_PER_S).astimezone()
    except (OSError, OverflowError, ValueError):
        logger.debug("Failed to localize %r, falling back to UTC", ts)
        local = datetime.datetime.fromtimestamp(
            ts / _MS_PER_S, tz=datetime.timezone.utc
        )
    return local.strftime("%Y-%m-%d %H:%M:%S.%f%z")


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def utc_timestamp() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp() * _MS_PER_S)
