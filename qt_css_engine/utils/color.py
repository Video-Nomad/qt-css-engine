"""Color math — Oklab premultiplied interpolation and shadow helpers."""

import math
import re

from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.types import ShadowParams

_RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)")
_HSL_RE = re.compile(r"hsla?\(\s*(\d+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%(?:\s*,\s*([\d.]+))?\s*\)")
_INSET_RE = re.compile(r"\binset\b", re.IGNORECASE)
_FUNC_COLOR_RE = re.compile(r"((?:rgba?|hsla?)\s*\([^)]*\))", re.IGNORECASE)
_LENGTH_TOKEN_RE = re.compile(r"(-?[\d.]+)(?:px|em|rem|%)?")


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


OklabPremul = tuple[float, float, float, float]


def to_oklab_premul(color: QColor) -> OklabPremul:
    L, A, B = _to_oklab(color.redF(), color.greenF(), color.blueF())
    alpha = color.alphaF()
    return (L * alpha, A * alpha, B * alpha, alpha)


def lerp_oklab_premul(c1: OklabPremul, c2: OklabPremul, t: float) -> QColor:
    L1_pre, A1_pre, B1_pre, a1 = c1
    L2_pre, A2_pre, B2_pre, a2 = c2
    a_out = a1 + (a2 - a1) * t
    L_out_pre = L1_pre + (L2_pre - L1_pre) * t
    A_out_pre = A1_pre + (A2_pre - A1_pre) * t
    B_out_pre = B1_pre + (B2_pre - B1_pre) * t
    if a_out > 0.0:
        L_out = L_out_pre / a_out
        A_out = A_out_pre / a_out
        B_out = B_out_pre / a_out
    else:
        L_out, A_out, B_out = 0.0, 0.0, 0.0
    r, g, b = _from_oklab(L_out, A_out, B_out)
    return QColor.fromRgbF(r, g, b, max(0.0, min(1.0, a_out)))


def interpolate_oklab(c1: QColor, c2: QColor, t: float) -> QColor:
    return lerp_oklab_premul(to_oklab_premul(c1), to_oklab_premul(c2), t)


def lerp_shadow(a: ShadowParams, b: ShadowParams, t: float) -> ShadowParams:
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
    c = QColor(params.color)
    c.setAlpha(0)
    return ShadowParams(params.offset_x, params.offset_y, params.blur, params.spread, c)


def parse_color(val: str) -> QColor:
    s = val.strip()
    c = QColor(s)
    if c.isValid():
        return c
    s_lower = s.lower()
    m = _RGB_RE.fullmatch(s_lower)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = round(float(m.group(4)) * 255) if m.group(4) is not None else 255
        return QColor(r, g, b, a)
    m = _HSL_RE.fullmatch(s_lower)
    if m:
        h = int(m.group(1))
        s_val = round(float(m.group(2)) * 255 / 100)
        l_val = round(float(m.group(3)) * 255 / 100)
        a = round(float(m.group(4)) * 255) if m.group(4) is not None else 255
        return QColor.fromHsl(h, s_val, l_val, a)
    return QColor()


def parse_box_shadow(val: str) -> ShadowParams | None:
    val = val.strip()
    if not val or val == "none":
        return None
    if _INSET_RE.search(val):
        return None
    depth = 0
    for i, ch in enumerate(val):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            val = val[:i]
            break
    color_str: str | None = None
    m = _FUNC_COLOR_RE.search(val)
    if m:
        color_str = m.group(1)
        val = (val[: m.start()] + val[m.end() :]).strip()
    lengths: list[float] = []
    for tok in val.split():
        num_m = _LENGTH_TOKEN_RE.fullmatch(tok)
        if num_m:
            lengths.append(float(num_m.group(1)))
        elif color_str is None and (tok.startswith("#") or tok.isalpha()):
            color_str = tok
    if len(lengths) < 2:
        return None
    color = parse_color(color_str) if color_str else QColor()
    if not color.isValid():
        color = QColor(0, 0, 0, 80)
    return ShadowParams(
        offset_x=lengths[0],
        offset_y=lengths[1],
        blur=lengths[2] if len(lengths) > 2 else 0.0,
        spread=lengths[3] if len(lengths) > 3 else 0.0,
        color=color,
    )
