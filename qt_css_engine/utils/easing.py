"""Easing curve factories — steps and cubic-bezier."""

import math

from qt_css_engine.qt_compat.QtCore import QEasingCurve, QPointF

_steps_curve_cache: dict[tuple[int, str], QEasingCurve] = {}


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
