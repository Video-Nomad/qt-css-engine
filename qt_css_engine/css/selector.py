"""Selector parsing — split trailing pseudo-classes from base selector."""

import re

from qt_css_engine.constants import ANIMATION_PSEUDOS, PSEUDO_ALIASES


def split_selector(selector: str) -> tuple[str, frozenset[str]]:
    """Return (base_selector, pseudo_set) for a single selector string."""
    m = re.search(r"((?:(?<!:):[a-z-]+)+)$", selector)
    if not m:
        return selector, frozenset()
    base = selector[: m.start()]
    found = [PSEUDO_ALIASES.get(p, p) for p in re.findall(r":[a-z-]+", m.group(1))]
    return base, frozenset(p for p in found if p in ANIMATION_PSEUDOS)
