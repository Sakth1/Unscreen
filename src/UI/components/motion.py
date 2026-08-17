"""Motion preferences and shared animation primitives.

M3 motion guidance (issue #62) requires subtle, non-flashing transitions
and fade-only motion when the user prefers reduced motion. flet 0.86.5 does
not expose an OS-level reduced-motion signal, so the preference is an
internal module flag flipped by the app or by tests.
"""

from __future__ import annotations

import flet as ft

from utils.flet_helpers import safe_update

#: Master switch for reduced-motion behavior: when enabled, transitions are
#: fade-only and skeleton loaders render as static placeholders.
REDUCED_MOTION = False

#: Skeleton pulse period in milliseconds (a single shimmer cycle).
SKELETON_PULSE_MS = 800

#: Animation curve used for skeleton pulsing (soft, non-flashing).
SKELETON_CURVE = ft.AnimationCurve.EASE_IN_OUT

#: Error banner fade-in duration in milliseconds.
ERROR_FADE_MS = 150

#: Empty-state fade-in duration in milliseconds.
EMPTY_FADE_MS = 200

#: Empty-state scale-in duration in milliseconds.
EMPTY_SCALE_MS = 200


def set_reduced_motion(enabled: bool) -> None:
    """Globally enable or disable reduced-motion behavior."""
    global REDUCED_MOTION
    REDUCED_MOTION = bool(enabled)


def is_reduced_motion() -> bool:
    """Return whether reduced-motion behavior is currently active."""
    return REDUCED_MOTION


def entrance_fade(duration_ms: int = EMPTY_FADE_MS) -> ft.Animation:
    """Fade-only entrance animation (the reduced-motion-safe fallback)."""
    return ft.Animation(duration_ms, ft.AnimationCurve.EASE_OUT)


def entrance(control: ft.Control, fade_ms: int = EMPTY_FADE_MS) -> None:
    """Fade (and scale) an already-mounted control into view.

    Runs a two-step opacity swap so the implicit animation fires on the
    live client; safe when detached (headless tests) via ``safe_update``.
    Scale-in is skipped when reduced motion is active.
    """
    control.animate_opacity = entrance_fade(fade_ms)
    control.opacity = 0
    if not is_reduced_motion():
        control.animate_scale = ft.Animation(EMPTY_SCALE_MS, ft.AnimationCurve.EASE_OUT)
        control.scale = 0.9
    safe_update(control)
    control.scale = 1.0
    control.opacity = 1
    safe_update(control)
