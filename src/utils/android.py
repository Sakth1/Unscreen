import logging
import os

logger = logging.getLogger(__name__)

_activity = None


def get_activity():
    """Return the Flet host Android activity, cached after first lookup.

    ``MAIN_ACTIVITY_HOST_CLASS_NAME`` is set by the Flet runtime when running
    on Android. Returns ``None`` when not running under Flet/Android.
    """
    global _activity
    if _activity is not None:
        logger.info("get_activity: returning cached activity=%s", _activity)
        return _activity
    activity_host_class = os.getenv("MAIN_ACTIVITY_HOST_CLASS_NAME")
    if not activity_host_class:
        logger.warning(
            "MAIN_ACTIVITY_HOST_CLASS_NAME not set — not running under Flet/Android?"
        )
        return None
    try:
        from jnius import autoclass  # type: ignore

        logger.info("get_activity: loading activity host class=%s", activity_host_class)
        activity_host = autoclass(activity_host_class)
        _activity = activity_host.mActivity
        logger.info("get_activity: resolved activity=%s", _activity)
        return _activity
    except Exception as e:
        logger.warning("Failed to get Android activity via jnius: %s", e)
        return None
