from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .css_parser import extract_rules
    from .engine import TransitionEngine

__all__ = [
    "TransitionEngine",
    "extract_rules",
]


# Lazy-load the TransitionEngine and extract_rules to avoid importing the wrong Qt wrapper too early
def __getattr__(name: str) -> object:
    if name == "TransitionEngine":
        from .engine import TransitionEngine

        return TransitionEngine
    if name == "extract_rules":
        from .css_parser import extract_rules

        return extract_rules
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
