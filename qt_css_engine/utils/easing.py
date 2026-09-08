"""Easing curves — factories plus CSS timing-function resolution."""

import math
import re

from qt_css_engine.constants import EASING_MAP
from qt_css_engine.qt_compat.QtCore import QEasingCurve, QPointF

__all__ = [
    "CUBIC_BEZIER_RE",
    "STEPS_RE",
    "make_cubic_bezier_curve",
    "make_steps_curve",
    "resolve_easing_curve",
]

_steps_curve_cache: dict[tuple[int, str], QEasingCurve] = {}

CUBIC_BEZIER_RE = re.compile(
    r"cubic-bezier\(\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*\)",
    re.IGNORECASE,
)

STEPS_RE = re.compile(
    r"steps\(\s*(\d+)(?:\s*,\s*(jump-start|jump-end|jump-none|jump-both|start|end))?\s*\)",
    re.IGNORECASE,
)


def make_steps_curve(n: int, position: str) -> QEasingCurve:
    pos = position.lower()
    if pos == "start":
        pos = "jump-start"
    elif pos == "end":
        pos = "jump-end"
    cache_key = (n, pos)
    cached = _steps_curve_cache.get(cache_key)
    if cached is not None:
        return cached
    if pos == "jump-start":

        def fn(t: float) -> float:
            return min(math.floor(t * n) + 1, n) / n
    elif pos == "jump-none":

        def fn(t: float) -> float:
            if n <= 1:
                return 0.0 if t < 1.0 else 1.0
            return min(math.floor(t * n), n - 1) / (n - 1)
    elif pos == "jump-both":

        def fn(t: float) -> float:
            return (math.floor(t * n) + 1) / (n + 1)
    else:

        def fn(t: float) -> float:
            return 1.0 if t >= 1.0 else math.floor(t * n) / n

    curve = QEasingCurve()
    curve.setCustomType(fn)
    _steps_curve_cache[cache_key] = curve
    return curve


def make_cubic_bezier_curve(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


def resolve_easing_curve(easing: str) -> QEasingCurve:
    """Parse a CSS timing-function string into a QEasingCurve."""
    if easing == "step-start":
        return make_steps_curve(1, "start")
    if easing == "step-end":
        return make_steps_curve(1, "end")
    if easing in EASING_MAP:
        return QEasingCurve(EASING_MAP[easing])
    if m := CUBIC_BEZIER_RE.match(easing):
        return make_cubic_bezier_curve(float(m[1]), float(m[2]), float(m[3]), float(m[4]))
    if m := STEPS_RE.match(easing):
        return make_steps_curve(int(m[1]), m[2] or "end")
    return QEasingCurve(QEasingCurve.Type.InOutQuad)
