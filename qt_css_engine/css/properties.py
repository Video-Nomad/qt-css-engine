"""Property capabilities shared by stylesheet cleanup and cascade expansion."""

from qt_css_engine.constants import EFFECT_PROPS, SUPPORTED_NUMERIC_PROPS


def is_animatable(prop: str) -> bool:
    """Whether the engine supports transitions for this property name."""
    return prop == "color" or prop.endswith("-color") or prop in EFFECT_PROPS or prop in SUPPORTED_NUMERIC_PROPS
