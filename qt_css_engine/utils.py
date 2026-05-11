import itertools
import math
import re

from .qt_compat.QtCore import QEasingCurve, QPointF
from .qt_compat.QtGui import QColor
from .qt_compat.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QStyle,
    QWidget,
)
from .types import ShadowParams

# Counter for generating unique IDs for scoped anim styles
_scope_counter = itertools.count(1)

# PyQt6 caps custom easing function registrations at 10 per process (PySide6 has no cap).
# Cache QEasingCurve objects by (n, position) so each unique step configuration registers
# exactly once, keeping PyQt6 well under the limit regardless of stylesheet complexity.
_steps_curve_cache: dict[tuple[int, str], QEasingCurve] = {}


def make_steps_curve(n: int, position: str) -> QEasingCurve:
    """Build (or return cached) a QEasingCurve for a CSS steps() timing function."""
    pos = position.lower()
    if pos == "start":
        pos = "jump-start"
    elif pos == "end":
        pos = "jump-end"

    cache_key = (n, pos)
    cached = _steps_curve_cache.get(cache_key)
    if cached is not None:
        return cached

    # fmt: off
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
    # fmt: on

    curve = QEasingCurve()
    curve.setCustomType(fn)
    _steps_curve_cache[cache_key] = curve
    return curve


def make_cubic_bezier_curve(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    """Build a QEasingCurve from CSS cubic-bezier() control points."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


def interpolate_oklab(c1: QColor, c2: QColor, t: float) -> QColor:
    """Interpolate two QColors in OKLab space with premultiplied alpha."""

    def _to_linear(c: float) -> float:
        return ((c + 0.055) / 1.055) ** 2.4 if c >= 0.04045 else c / 12.92

    def _to_srgb(c: float) -> float:
        return 1.055 * (c ** (1.0 / 2.4)) - 0.055 if c >= 0.0031308 else 12.92 * c

    def _cbrt(x: float) -> float:
        return math.copysign(abs(x) ** (1.0 / 3.0), x)

    def _to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
        l = _to_linear(r)
        m = _to_linear(g)
        s = _to_linear(b)
        lms_l = 0.4122214708 * l + 0.5363325363 * m + 0.0514459929 * s
        lms_m = 0.2119034982 * l + 0.6806995451 * m + 0.1073969566 * s
        lms_s = 0.0883024619 * l + 0.2817188376 * m + 0.6299787005 * s
        l_, m_, s_ = _cbrt(lms_l), _cbrt(lms_m), _cbrt(lms_s)
        return (
            0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
        )

    def _from_oklab(L: float, A: float, B: float) -> tuple[float, float, float]:
        l_ = L + 0.3963377774 * A + 0.2158037573 * B
        m_ = L - 0.1055613458 * A - 0.0638541728 * B
        s_ = L - 0.0894841775 * A - 1.2914855480 * B
        l, m, s = l_**3, m_**3, s_**3
        r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
        g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
        b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
        return (
            _to_srgb(max(0.0, min(1.0, r))),
            _to_srgb(max(0.0, min(1.0, g))),
            _to_srgb(max(0.0, min(1.0, b))),
        )

    L1, A1, B1 = _to_oklab(c1.redF(), c1.greenF(), c1.blueF())
    L2, A2, B2 = _to_oklab(c2.redF(), c2.greenF(), c2.blueF())

    a1 = c1.alphaF()
    a2 = c2.alphaF()

    # Premultiply the OKLab channels by their respective alphas
    L1_pre, A1_pre, B1_pre = L1 * a1, A1 * a1, B1 * a1
    L2_pre, A2_pre, B2_pre = L2 * a2, A2 * a2, B2 * a2

    # Interpolate the premultiplied values and the alpha
    a_out = a1 + (a2 - a1) * t
    L_out_pre = L1_pre + (L2_pre - L1_pre) * t
    A_out_pre = A1_pre + (A2_pre - A1_pre) * t
    B_out_pre = B1_pre + (B2_pre - B1_pre) * t

    # Un-premultiply by dividing by the new interpolated alpha
    if a_out > 0.0:
        L_out = L_out_pre / a_out
        A_out = A_out_pre / a_out
        B_out = B_out_pre / a_out
    else:
        # Prevent division by zero if the resulting color is completely transparent.
        # When alpha is 0, the RGB values are invisible anyway, so 0.0 is safe.
        L_out, A_out, B_out = 0.0, 0.0, 0.0

    r, g, b = _from_oklab(L_out, A_out, B_out)

    return QColor.fromRgbF(r, g, b, max(0.0, min(1.0, a_out)))


def lerp_shadow(a: ShadowParams, b: ShadowParams, t: float) -> ShadowParams:
    """Linearly interpolate between two ShadowParams at progress t [0, 1]."""

    def lerp(x: float, y: float) -> float:
        return x + (y - x) * t

    return ShadowParams(
        offset_x=lerp(a.offset_x, b.offset_x),
        offset_y=lerp(a.offset_y, b.offset_y),
        blur=lerp(a.blur, b.blur),
        spread=lerp(a.spread, b.spread),
        color=interpolate_oklab(a.color, b.color, t),
    )


def shadow_as_transparent(params: ShadowParams) -> ShadowParams:
    """Return a copy of params with alpha=0 (used as the invisible end for fade-in/out)."""
    c = QColor(params.color)
    c.setAlpha(0)
    return ShadowParams(params.offset_x, params.offset_y, params.blur, params.spread, c)


def parse_color(val: str) -> QColor:
    """
    Parse a CSS color string into a QColor.

    Handles hex and named colors (via QColor directly), plus rgb(), rgba(),
    hsl(), and hsla() functional notations that Qt's QColor constructor rejects.
    Returns an invalid QColor if the string is unrecognised.
    """
    s = val.strip()

    c = QColor(s)
    if c.isValid():
        return c

    s_lower = s.lower()

    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)", s_lower)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = round(float(m.group(4)) * 255) if m.group(4) is not None else 255
        return QColor(r, g, b, a)

    m = re.fullmatch(r"hsla?\(\s*(\d+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%(?:\s*,\s*([\d.]+))?\s*\)", s_lower)
    if m:
        h = int(m.group(1))
        s_val = round(float(m.group(2)) * 255 / 100)
        l_val = round(float(m.group(3)) * 255 / 100)
        a = round(float(m.group(4)) * 255) if m.group(4) is not None else 255
        return QColor.fromHsl(h, s_val, l_val, a)

    return QColor()  # invalid


def parse_box_shadow(val: str) -> ShadowParams | None:
    """
    Parse a CSS box-shadow value into ShadowParams.

    Supports single outer shadow: <offset-x> <offset-y> [<blur>] [<spread>] [<color>]
    Returns None for 'none' or inset shadows.
    Multiple comma-separated shadows: first non-inset shadow wins.
    """
    val = val.strip()
    if not val or val == "none":
        return None
    if re.search(r"\binset\b", val, re.IGNORECASE):
        return None

    # Take first comma-separated shadow. Can't naively split on ',' because rgba() contains
    # commas, so find the first comma that isn't inside parentheses.
    depth = 0
    for i, ch in enumerate(val):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            val = val[:i]
            break

    # Extract functional color notation before splitting on spaces.
    color_str: str | None = None
    m = re.search(r"((?:rgba?|hsla?)\s*\([^)]*\))", val, re.IGNORECASE)
    if m:
        color_str = m.group(1)
        val = (val[: m.start()] + val[m.end() :]).strip()

    lengths: list[float] = []
    for tok in val.split():
        num_m = re.fullmatch(r"(-?[\d.]+)(?:px|em|rem|%)?", tok)
        if num_m:
            lengths.append(float(num_m.group(1)))
        elif color_str is None and (tok.startswith("#") or tok.isalpha()):
            color_str = tok

    if len(lengths) < 2:
        return None

    color = parse_color(color_str) if color_str else QColor()
    if not color.isValid():
        color = QColor(0, 0, 0, 80)  # semi-transparent black default

    return ShadowParams(
        offset_x=lengths[0],
        offset_y=lengths[1],
        blur=lengths[2] if len(lengths) > 2 else 0.0,
        spread=lengths[3] if len(lengths) > 3 else 0.0,
        color=color,
    )


def parse_css_val(val: str | None) -> int | float | str | None:
    if not val:
        return None
    clean = val.replace("px", "").strip()
    try:
        if "." in clean:
            return float(clean)
        return int(clean)
    except ValueError:
        return val


_CSS_NUMERIC_RE = re.compile(r"^\s*(-?[\d.]+)\s*(px|pt|em|rem|%|)\s*$")


def parse_css_numeric(val: str | None) -> tuple[float, str] | None:
    """
    Parse a CSS numeric value into (number, unit).

    Recognises px, pt, em, rem, % and bare numbers (treated as px).
    Returns None when the value is absent or non-numeric.
    """
    if not val:
        return None
    m = _CSS_NUMERIC_RE.match(val.strip())
    if not m:
        return None
    try:
        return (float(m.group(1)), m.group(2) or "px")
    except ValueError:
        return None


def scoped_anim_style(widget: QWidget, props: dict[str, str]) -> str:
    """
    Build an inline stylesheet scoped to exactly this widget via a property selector.

    An unscoped rule like ``background-color: red;`` would cascade to all descendants.
    Scoping with ``WidgetType[_anim_scope="<id>"]`` confines it to the one widget that
    has the property set, because children never have the same unique value.

    Sets ``_anim_scope`` on the widget lazily so all call sites are covered regardless
    of whether the widget was ever added to the watch set.
    """
    scope_id: str = widget.property("_anim_scope") or ""
    if not scope_id:
        scope_id = str(next(_scope_counter))
        widget.setProperty("_anim_scope", scope_id)
    props_str = " ".join(f"{p}: {v};" for p, v in props.items())
    selector = f'{type(widget).__name__}[_anim_scope="{scope_id}"]'
    return f"{selector} {{ {props_str} }}"


def apply_opacity_to_widget(widget: QWidget, value: float, priority: str) -> None:
    """
    Write an opacity value into the widget's graphics-effect slot.

    Updates the existing QGraphicsOpacityEffect in-place when possible.
    Removes the effect when value reaches 1.0 (no-op is cheaper than a pass-through effect).
    Yields the slot to a QGraphicsDropShadowEffect when priority != "opacity".

    When opacity reaches 1.0 and a desired shadow is stored on the widget (set by
    apply_shadow_to_widget while opacity held the slot), the shadow is installed so
    it becomes visible exactly when the widget becomes fully opaque.
    """
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        if value >= 1.0:
            widget.setGraphicsEffect(None)
            desired: ShadowParams | None = getattr(widget, "_desired_shadow", None)
            if desired is not None:
                shadow_eff = QGraphicsDropShadowEffect(widget)
                shadow_eff.setOffset(desired.offset_x, desired.offset_y)
                shadow_eff.setBlurRadius(desired.blur)
                shadow_eff.setColor(desired.color)
                widget.setGraphicsEffect(shadow_eff)
        else:
            effect.setOpacity(value)
        return
    if isinstance(effect, QGraphicsDropShadowEffect) and priority != "opacity":
        return  # shadow holds the slot; opacity is a no-op
    if value < 1.0:
        op = QGraphicsOpacityEffect(widget)
        op.setOpacity(value)
        widget.setGraphicsEffect(op)


def apply_shadow_to_widget(widget: QWidget, params: ShadowParams | None, priority: str) -> None:
    """
    Write shadow params into the widget's graphics-effect slot.

    Updates the existing QGraphicsDropShadowEffect in-place when possible (no allocation,
    Qt invalidates its internal blur cache automatically on each setter call).
    Yields the slot to a QGraphicsOpacityEffect when priority != "box-shadow".

    Always writes params to widget._desired_shadow so that apply_opacity_to_widget can
    restore the shadow when opacity finishes animating to 1.0.
    """
    setattr(widget, "_desired_shadow", params)
    effect = widget.graphicsEffect()
    if params is None:
        if isinstance(effect, QGraphicsDropShadowEffect):
            widget.setGraphicsEffect(None)
        return
    if isinstance(effect, QGraphicsDropShadowEffect):
        # In-place update — no allocation per tick.
        effect.setOffset(params.offset_x, params.offset_y)
        effect.setBlurRadius(params.blur)
        effect.setColor(params.color)
        return
    if isinstance(effect, QGraphicsOpacityEffect) and priority != "box-shadow":
        return  # opacity holds the slot; desired_shadow stored above for restore on opacity=1.0
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setOffset(params.offset_x, params.offset_y)
    shadow.setBlurRadius(params.blur)
    shadow.setColor(params.color)
    widget.setGraphicsEffect(shadow)


# Layout helpers


def padding_side_px(base_props: dict[str, str], side: str) -> int:
    """Return the QSS padding in pixels for one side ('left', 'right', 'top', 'bottom')."""
    raw = base_props.get(f"padding-{side}") or base_props.get("padding") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def margin_side_px(base_props: dict[str, str], side: str) -> int:
    """Return the QSS margin in pixels for one side ('left', 'right', 'top', 'bottom')."""
    raw = base_props.get(f"margin-{side}") or base_props.get("margin") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def _border_side_px(base_props: dict[str, str], side: str) -> int:
    """Return the QSS border width in pixels for one side ('left', 'right', 'top', 'bottom')."""
    raw = base_props.get(f"border-{side}-width") or base_props.get("border-width") or "0"
    v = parse_css_val(raw)
    return int(v) if isinstance(v, (int, float)) else 0


def total_border_px(widget: QWidget, base_props: dict[str, str], side: str) -> int:
    """
    Return the effective border width for one side, accounting for native platform borders.

    When any border property is present in QSS, QStyleSheetStyle controls border drawing and
    the QSS value is authoritative (may be 0 for 'border: none').
    When no border property is in QSS at all, the native platform border is used:
    - QFrame subclasses: use widget.frameWidth() — reflects the actual frame drawn (0 for NoFrame).
      PM_DefaultFrameWidth is a style-level metric that does not match the actual rendered frame for
      widgets like QLabel (NoFrame), causing the natural-size calculation to undercount by PM_DefaultFrameWidth
      per side and producing a snap at animation end.
    - Other widgets (QPushButton, QLineEdit, …): use PM_DefaultFrameWidth as before.

    Qt's contentsMargins() tracks only QSS padding, so contentsRect() = content + effective_border.
    Subtracting this value gives the true CSS content-box size that min-width/max-width operate on.
    """
    if any(k.startswith("border") for k in base_props):
        return _border_side_px(base_props, side)
    if isinstance(widget, QFrame):
        return max(0, widget.frameWidth())
    style = widget.style() or QApplication.style()
    if style is None:
        return 0
    fw = style.pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, widget)
    return max(0, fw)


def content_box_px(widget: QWidget, base_props: dict[str, str], prop: str, pixel_value: int) -> int:
    """
    Subtract border+padding+margin from a raw pixel size to get the content-area size.

    ``prop`` must contain ``"width"`` or ``"height"`` to select the axis.
    The result may be negative if box-model extras exceed ``pixel_value``; callers clamp as needed.

    For QFrame-derived widgets (QLabel, QFrame, …) Qt reflects the full QSS box-model
    (border + padding + margin) in widget.contentsRect(); we trust that delta directly.
    Reading the same values from base_props would double-count, because QLabel's frameWidth()
    already bakes padding+margin into the frame.

    For non-QFrame widgets (QPushButton, QLineEdit, …) contentsRect() does NOT reflect QSS
    padding/border, so we compute from the CSS values plus PM_DefaultFrameWidth for the
    native frame when no border is declared.

    Note: Qt's layout operates in integer logical pixels, so this calculation should be exact in practice
    even under fractional OS scaling.
    """
    if isinstance(widget, QFrame):
        cr = widget.contentsRect()
        if "width" in prop:
            extras = max(0, widget.width() - cr.width())
        else:
            extras = max(0, widget.height() - cr.height())
        return pixel_value - extras
    if "width" in prop:
        b = total_border_px(widget, base_props, "left") + total_border_px(widget, base_props, "right")
        p = padding_side_px(base_props, "left") + padding_side_px(base_props, "right")
        m = margin_side_px(base_props, "left") + margin_side_px(base_props, "right")
    else:
        b = total_border_px(widget, base_props, "top") + total_border_px(widget, base_props, "bottom")
        p = padding_side_px(base_props, "top") + padding_side_px(base_props, "bottom")
        m = margin_side_px(base_props, "top") + margin_side_px(base_props, "bottom")
    return pixel_value - b - p - m


def get_preferred_size_fallback(widget: QWidget, base_props: dict[str, str], prop: str) -> str:
    """Return the widget's natural size as a CSS pixel value for the given size property."""
    hint = widget.sizeHint()
    px = hint.width() if "width" in prop else hint.height()
    return f"{max(0, content_box_px(widget, base_props, prop, px))}px"


def update_shadow_ancestor(widget: QWidget) -> None:
    """Force a full repaint on the nearest ancestor with a QGraphicsEffect.

    When a child's inline stylesheet changes during animation, Qt propagates a dirty
    region equal to the child's bounding rect. A QGraphicsDropShadowEffect on an
    ancestor casts shadow pixels *outside* that rect (offset + blur), so those pixels
    are never cleared and appear as residual outlines. Calling update() on the
    effect-bearing ancestor invalidates its full offscreen pixmap and forces a clean
    re-render including the shadow region.
    """
    w = widget.parentWidget()
    while w is not None:
        if w.graphicsEffect() is not None:
            w.update()
            return
        w = w.parentWidget()
