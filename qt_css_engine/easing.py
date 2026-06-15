import re

from qt_css_engine.constants import EASING_MAP
from qt_css_engine.qt_compat.QtCore import QEasingCurve
from qt_css_engine.utils import make_cubic_bezier_curve, make_steps_curve

CUBIC_BEZIER_RE = re.compile(
    r"cubic-bezier\(\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\s*\)",
    re.IGNORECASE,
)

STEPS_RE = re.compile(
    r"steps\(\s*(\d+)(?:\s*,\s*(jump-start|jump-end|jump-none|jump-both|start|end))?\s*\)",
    re.IGNORECASE,
)


def resolve_easing_curve(easing: str) -> QEasingCurve:
    """Parse a CSS timing-function string into a QEasingCurve."""
    if m := CUBIC_BEZIER_RE.match(easing):
        return make_cubic_bezier_curve(float(m[1]), float(m[2]), float(m[3]), float(m[4]))
    if m := STEPS_RE.match(easing):
        return make_steps_curve(int(m[1]), m[2] or "end")
    if easing == "step-start":
        return make_steps_curve(1, "start")
    if easing == "step-end":
        return make_steps_curve(1, "end")
    return QEasingCurve(EASING_MAP.get(easing, QEasingCurve.Type.InOutQuad))
