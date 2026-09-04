from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Union

from qt_css_engine.qt_compat.QtGui import QColor
from qt_css_engine.state.widget_state import WidgetState

if TYPE_CHECKING:
    from .animation.color import ColorAnimation
    from .animation.numeric import GenericPropertyAnimation
    from .animation.opacity import OpacityAnimation
    from .animation.shadow import BoxShadowHandle
    from .css.model import TransitionSpec

Animation = Union["ColorAnimation", "OpacityAnimation", "GenericPropertyAnimation", "BoxShadowHandle"]


@dataclass
class ResolvedRuleState:
    """CSS values and transitions selected for one widget evaluation."""

    base_props: dict[str, str] = field(default_factory=dict)
    target_props: dict[str, str] = field(default_factory=dict)
    transitions: dict[str, TransitionSpec] = field(default_factory=dict)
    animated_props: set[str] = field(default_factory=set)


class InternalWriteReason(Enum):
    """Why the engine is temporarily suppressing event evaluation during internal mutations."""

    CLASS_CHANGE = auto()
    MEASURE = auto()


@dataclass(frozen=True)
class ResolvedProperty:
    """Values needed to decide how one CSS property should change."""

    animation: Animation | None
    current: str
    target: str
    is_natural_target: bool
    spec: TransitionSpec | None


WidgetContext = WidgetState


@dataclass
class ShadowParams:
    """Decomposed CSS box-shadow parameters used for interpolation."""

    offset_x: float = 0.0
    offset_y: float = 4.0
    blur: float = 8.0
    spread: float = 0.0
    color: QColor = field(default_factory=lambda: QColor(0, 0, 0, 80))
