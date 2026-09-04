"""CSS gradient → Qt gradient translation.

Qt's QSS engine supports gradients via its own proprietary syntax which is not
compatible with standard CSS gradient functions.  This module bridges that gap so
users can write standard CSS and have it automatically rewritten to Qt form:

    linear-gradient()  →  qlineargradient()
    radial-gradient()  →  qradialgradient()
    conic-gradient()   →  qconicalgradient()

Note: gradient values are static — they are translated and passed through to Qt as-is.
Animating a gradient ``background-color`` with ``transition:`` is not supported; the
animation will be silently skipped and the gradient rendered without interpolation.
"""

import math
import re

# --- Direction helpers -------------------------------------------------------

# fmt: off
_DIRECTION_MAP: dict[str, tuple[float, float, float, float]] = {
    "to right":        (0.0, 0.0, 1.0, 0.0),
    "to left":         (1.0, 0.0, 0.0, 0.0),
    "to bottom":       (0.0, 0.0, 0.0, 1.0),
    "to top":          (0.0, 1.0, 0.0, 0.0),
    "to bottom right": (0.0, 0.0, 1.0, 1.0),
    "to right bottom": (0.0, 0.0, 1.0, 1.0),
    "to bottom left":  (1.0, 0.0, 0.0, 1.0),
    "to left bottom":  (1.0, 0.0, 0.0, 1.0),
    "to top right":    (0.0, 1.0, 1.0, 0.0),
    "to right top":    (0.0, 1.0, 1.0, 0.0),
    "to top left":     (1.0, 1.0, 0.0, 0.0),
    "to left top":     (1.0, 1.0, 0.0, 0.0),
}
# fmt: on

_DEFAULT_DIRECTION: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)  # to bottom

_ANGLE_RE = re.compile(r"^(-?[\d.]+)deg$")


def _parse_direction(arg: str) -> tuple[float, float, float, float] | None:
    """Return (x1, y1, x2, y2) for a direction token, or None if not a direction."""
    d = arg.strip().lower()
    if d in _DIRECTION_MAP:
        return _DIRECTION_MAP[d]
    m = _ANGLE_RE.match(d)
    if m:
        # CSS angles: 0deg = to top, 90deg = to right, clockwise.
        angle_rad = math.radians(float(m.group(1)))
        x2 = 0.5 + 0.5 * math.sin(angle_rad)
        y2 = 0.5 - 0.5 * math.cos(angle_rad)
        return round(1.0 - x2, 6), round(1.0 - y2, 6), round(x2, 6), round(y2, 6)
    return None


# --- Argument splitting ------------------------------------------------------


def _split_args(s: str) -> list[str]:
    """Split a string by top-level commas (ignores commas inside nested parentheses)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


# --- Stop parsing ------------------------------------------------------------

_STOP_POSITION_RE = re.compile(r"\s+([\d.]+)(%?)\s*$")


def _parse_stop(s: str) -> tuple[float | None, str]:
    """Parse a color-stop string into (position_0_to_1_or_None, color_str).

    Examples:
        "red"         → (None, "red")
        "red 25%"     → (0.25, "red")
        "#ff0000 0.5" → (0.5,  "#ff0000")
    """
    s = s.strip()
    m = _STOP_POSITION_RE.search(s)
    if m:
        val = float(m.group(1))
        position = val / 100.0 if m.group(2) == "%" else val
        return position, s[: m.start()].strip()
    return None, s


def _fill_positions(raw: list[tuple[float | None, str]]) -> list[tuple[float, str]]:
    """Fill missing stop positions by linear interpolation between defined neighbours."""
    n = len(raw)
    positions: list[float | None] = [p for p, _ in raw]

    # Anchor first and last
    if positions[0] is None:
        positions[0] = 0.0
    if positions[-1] is None:
        positions[-1] = 1.0

    # Fill interior gaps
    i = 0
    while i < n:
        if positions[i] is None:
            j = i + 1
            while j < n and positions[j] is None:
                j += 1
            _ps = positions[i - 1]
            p_start: float = _ps if _ps is not None else 0.0
            _pe = positions[j]
            p_end: float = _pe if _pe is not None else 1.0
            count = j - i + 1
            for k in range(i, j):
                positions[k] = p_start + (p_end - p_start) * (k - i + 1) / count
        i += 1

    return [(float(p) if p is not None else 0.0, color) for p, (_, color) in zip(positions, raw)]


# --- Per-type inner translators ----------------------------------------------


def _translate_linear_inner(inner: str) -> str | None:
    """Translate the interior of a ``linear-gradient(...)`` call to ``qlineargradient``."""
    args = _split_args(inner)
    if not args:
        return None

    direction = _parse_direction(args[0])
    stop_args = args[1:] if direction is not None else args
    if direction is None:
        direction = _DEFAULT_DIRECTION

    if len(stop_args) < 2:
        return None

    x1, y1, x2, y2 = direction
    stops = _fill_positions([_parse_stop(s) for s in stop_args])
    stop_strs = [f"stop:{pos:.4g} {color}" for pos, color in stops]
    return f"qlineargradient(x1:{x1:.4g}, y1:{y1:.4g}, x2:{x2:.4g}, y2:{y2:.4g}, " + ", ".join(stop_strs) + ")"


# Keywords that identify the optional first argument of radial-gradient() as a
# shape/size/position descriptor rather than a color stop.
_RADIAL_DESCRIPTOR_KEYWORDS: frozenset[str] = frozenset(
    {"circle", "ellipse", "at", "closest-side", "farthest-side", "closest-corner", "farthest-corner"}
)

_POS_VALUE_RE = re.compile(r"^(-?[\d.]+)(%?)$")


def _parse_pos_value(s: str) -> float:
    """Parse a CSS position token (``50%`` or ``0.5``) to a [0, 1] float."""
    m = _POS_VALUE_RE.match(s.strip())
    if not m:
        return 0.5
    val = float(m.group(1))
    return val / 100.0 if m.group(2) == "%" else val


def _translate_radial_inner(inner: str) -> str | None:
    """
    Translate the interior of a ``radial-gradient(...)`` call to ``qradialgradient``.

    Supported descriptor syntax (optional first argument):
        [circle|ellipse] [<size>] [at <cx> <cy>]

    where ``<size>`` may be a percentage, a plain number (0–1), or a size keyword
    (``closest-side`` etc.; keywords are accepted but ignored — Qt has no equivalent).
    Defaults: cx=0.5, cy=0.5, radius=0.5.  The focal point (fx, fy) is set to the
    centre so the gradient behaves like a standard CSS radial-gradient.
    """
    args = _split_args(inner)
    if not args:
        return None

    cx, cy, radius = 0.5, 0.5, 0.5
    stop_start = 0

    first_words = set(re.findall(r"[a-z-]+", args[0].lower()))
    if first_words & _RADIAL_DESCRIPTOR_KEYWORDS:
        stop_start = 1
        descriptor = args[0]

        # Extract 'at <cx> <cy>'
        at_m = re.search(r"\bat\s+([\d.]+%?)\s+([\d.]+%?)", descriptor, re.IGNORECASE)
        if at_m:
            cx = _parse_pos_value(at_m.group(1))
            cy = _parse_pos_value(at_m.group(2))
            descriptor = descriptor[: at_m.start()]

        # Extract explicit radius: strip shape/size keywords, look for a number
        cleaned = re.sub(
            r"\b(circle|ellipse|closest-side|farthest-side|closest-corner|farthest-corner)\b",
            "",
            descriptor,
            flags=re.IGNORECASE,
        ).strip()
        size_m = re.search(r"([\d.]+)(%?)", cleaned)
        if size_m:
            val = float(size_m.group(1))
            radius = val / 100.0 if size_m.group(2) == "%" else val

    stop_args = args[stop_start:]
    if len(stop_args) < 2:
        return None

    stops = _fill_positions([_parse_stop(s) for s in stop_args])
    stop_strs = [f"stop:{pos:.4g} {color}" for pos, color in stops]
    return (
        f"qradialgradient(cx:{cx:.4g}, cy:{cy:.4g}, radius:{radius:.4g}, "
        f"fx:{cx:.4g}, fy:{cy:.4g}, " + ", ".join(stop_strs) + ")"
    )


def _translate_conic_inner(inner: str) -> str | None:
    """
    Translate the interior of a ``conic-gradient(...)`` call to ``qconicalgradient``.

    Supported descriptor syntax (optional first argument):
        [from <angle>] [at <cx> <cy>]

    Defaults: cx=0.5, cy=0.5, angle=0.
    """
    args = _split_args(inner)
    if not args:
        return None

    cx, cy, angle = 0.5, 0.5, 0.0
    stop_start = 0

    first_lower = args[0].lower()
    if "from" in first_lower.split() or "at" in first_lower.split():
        stop_start = 1
        descriptor = args[0]

        from_m = re.search(r"\bfrom\s+(-?[\d.]+)deg\b", descriptor, re.IGNORECASE)
        if from_m:
            angle = float(from_m.group(1))

        at_m = re.search(r"\bat\s+([\d.]+%?)\s+([\d.]+%?)", descriptor, re.IGNORECASE)
        if at_m:
            cx = _parse_pos_value(at_m.group(1))
            cy = _parse_pos_value(at_m.group(2))

    stop_args = args[stop_start:]
    if len(stop_args) < 2:
        return None

    stops = _fill_positions([_parse_stop(s) for s in stop_args])
    stop_strs = [f"stop:{pos:.4g} {color}" for pos, color in stops]
    return f"qconicalgradient(cx:{cx:.4g}, cy:{cy:.4g}, angle:{angle:.4g}, " + ", ".join(stop_strs) + ")"


# --- Main translation function -----------------------------------------------

_GRADIENT_RE = re.compile(r"\b(linear|radial|conic)-gradient\s*\(", re.IGNORECASE)

_GRADIENT_TRANSLATORS = {
    "linear": _translate_linear_inner,
    "radial": _translate_radial_inner,
    "conic": _translate_conic_inner,
}


def translate_gradients(value: str) -> str:
    """
    Translate all CSS gradient functions in *value* to their Qt equivalents.

    Handles ``linear-gradient()``, ``radial-gradient()``, and ``conic-gradient()``.
    Returns *value* unchanged for calls that cannot be parsed (rather than raising).
    """
    result = value
    offset = 0

    for m in _GRADIENT_RE.finditer(value):
        gradient_type = m.group(1).lower()
        fn_start = m.start() + offset
        paren_open = m.end() - 1 + offset

        # Find matching closing paren
        depth = 0
        paren_close = -1
        for i in range(paren_open, len(result)):
            if result[i] == "(":
                depth += 1
            elif result[i] == ")":
                depth -= 1
                if depth == 0:
                    paren_close = i
                    break

        if paren_close == -1:
            continue  # unmatched parenthesis — skip

        inner = result[paren_open + 1 : paren_close]
        translator = _GRADIENT_TRANSLATORS[gradient_type]
        qt_gradient = translator(inner)
        if qt_gradient is None:
            continue

        replacement_len = paren_close - fn_start + 1
        result = result[:fn_start] + qt_gradient + result[paren_close + 1 :]
        offset += len(qt_gradient) - replacement_len

    return result
