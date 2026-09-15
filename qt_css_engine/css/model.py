"""CSS model types — TransitionSpec and StyleRule."""

from dataclasses import dataclass, field

from qt_css_engine.constants import BORDER_RADIUS_PROPS, EFFECT_PROPS


@dataclass
class TransitionSpec:
    """Parsed CSS transition declaration for one property."""

    prop: str
    duration_ms: int
    easing: str = "ease"
    delay_ms: int = 0


@dataclass
class StyleRule:
    """One parsed CSS rule block with selector metadata and transition specs."""

    selector: str
    base_selector: str
    properties: dict[str, str]
    pseudo_set: frozenset[str] = field(default_factory=frozenset)
    transitions: list[TransitionSpec] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    subcontrol: bool = False
    has_attrs: bool = False
    # CSS2 a-b-c specificity (Qt docs follow CSS2) + source order for ties.
    specificity: tuple[int, int, int] = (0, 0, 0)
    order: int = 0

    has_effect_props: bool = field(init=False, default=False)
    has_cursor_prop: bool = field(init=False, default=False)
    has_border_radius_props: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        keys = self.properties.keys()
        self.has_effect_props = not keys.isdisjoint(EFFECT_PROPS)
        self.has_cursor_prop = "cursor" in keys
        self.has_border_radius_props = not keys.isdisjoint(BORDER_RADIUS_PROPS)
