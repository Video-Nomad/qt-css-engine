"""CSS value parsing — cached numeric parsers."""

import re
from functools import lru_cache


@lru_cache(maxsize=4096)
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


@lru_cache(maxsize=4096)
def parse_css_numeric(val: str | None) -> tuple[float, str] | None:
    if not val:
        return None
    m = _CSS_NUMERIC_RE.match(val.strip())
    if not m:
        return None
    try:
        return (float(m.group(1)), m.group(2) or "px")
    except ValueError:
        return None
