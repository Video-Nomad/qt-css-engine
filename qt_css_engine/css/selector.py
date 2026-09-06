"""Selector parsing — split trailing pseudo-classes from base selector."""

import re

from qt_css_engine.constants import ANIMATION_PSEUDOS, PSEUDO_ALIASES

_TRAILING_PSEUDOS_RE = re.compile(r"((?:(?<!:):[a-z-]+)+)$")
_PSEUDO_RE = re.compile(r":[a-z-]+")


def split_selector(selector: str) -> tuple[str, frozenset[str]]:
    """Return (base_selector, pseudo_set) for a single selector string."""
    m = _TRAILING_PSEUDOS_RE.search(selector)
    if not m:
        return selector, frozenset()
    base = selector[: m.start()]
    found = [PSEUDO_ALIASES.get(p, p) for p in _PSEUDO_RE.findall(m.group(1))]
    return base, frozenset(p for p in found if p in ANIMATION_PSEUDOS)
