"""Display-aware UI sizing without platform-specific dependencies."""

from __future__ import annotations


MIN_UI_SCALE = 0.75
MAX_UI_SCALE = 2.50


def _clamp(value: float) -> float:
    return max(MIN_UI_SCALE, min(float(value), MAX_UI_SCALE))


def resolve_ui_scale(
    screen_width: int,
    screen_height: int,
    configured_scale: float | None = None,
    *,
    dpi_scale: float | None = None,
) -> float:
    """Resolve a safe geometry scale from resolution and optional display DPI."""
    if configured_scale is not None:
        try:
            return round(_clamp(float(configured_scale)), 2)
        except (TypeError, ValueError):
            pass

    try:
        width = max(1, int(screen_width))
        height = max(1, int(screen_height))
    except (TypeError, ValueError):
        return 1.0

    # Preserve the historical size at 1920x1080 and below. Scaling from both
    # axes avoids making ultrawide or unusually short displays excessive.
    automatic = min(width / 1920.0, height / 1080.0)
    try:
        if dpi_scale is not None:
            automatic = max(automatic, float(dpi_scale))
    except (TypeError, ValueError):
        pass
    return round(max(1.0, min(automatic, 2.0)), 2)


def scale_px(value: int | float, scale: float) -> int:
    return max(1, int(round(float(value) * float(scale))))


__all__ = ["MAX_UI_SCALE", "MIN_UI_SCALE", "resolve_ui_scale", "scale_px"]
