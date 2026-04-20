from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Union

from .qt_compat.QtCore import QTimer
from .qt_compat.QtGui import QColor

if TYPE_CHECKING:
    from .handlers import BoxShadowHandle, ColorAnimation, GenericPropertyAnimation, OpacityAnimation

Animation = Union["ColorAnimation", "OpacityAnimation", "GenericPropertyAnimation", "BoxShadowHandle"]


class EvaluationCause(Enum):
    """Why the engine is evaluating a widget's CSS state right now."""

    DIRECT = auto()
    POLISH = auto()
    PSEUDO_STATE = auto()
    CLASS_CHANGE = auto()
    CLASS_ANIMATION_FINISH = auto()
    WINDOW_DEACTIVATE = auto()
    RULE_RELOAD = auto()
    DELAY_FIRE = auto()

    @property
    def snaps_transitions(self) -> bool:
        """True when animations should snap to target immediately rather than run."""
        return self is EvaluationCause.POLISH

    @property
    def is_class_driven(self) -> bool:
        """True when the evaluation was triggered by a class property change."""
        return self is EvaluationCause.CLASS_CHANGE


class InternalWriteReason(Enum):
    """Why the engine is temporarily suppressing event evaluation during internal mutations."""

    CLASS_CHANGE = auto()
    MEASURE = auto()


@dataclass
class WidgetContext:
    """Per-widget state tracked by the transition engine."""

    active_pseudos: set[str] = field(default_factory=lambda: set[str]())
    css_anim_props: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    active_animations: dict[str, Animation] = field(default_factory=lambda: dict[str, Animation]())
    internal_write_depth: int = 0
    internal_write_reason: InternalWriteReason | None = None
    pre_polish_size: tuple[int, int] | None = None
    # Properties with in-flight class-change-initiated animations.
    # Pseudo-state changes (hover/focus) defer to these until they finish.
    class_anim_props: set[str] = field(default_factory=lambda: set[str]())
    class_anim_gen: int = 0
    # Per-property delay timers scheduled by transition-delay declarations.
    # Cancelled whenever the widget state changes before the timer fires.
    pending_delays: dict[str, QTimer] = field(default_factory=lambda: dict[str, QTimer]())
    # Last cursor value applied via setCursor() — None means unsetCursor() (Qt default).
    applied_cursor: str | None = None
    # Per-property class-anim finished callbacks stored so they can be disconnected before
    # reconnecting — prevents accumulation of stale closures on rapid class changes.
    class_anim_callbacks: dict[str, Callable[[], None]] = field(default_factory=lambda: dict[str, Callable[[], None]]())


@dataclass
class ShadowParams:
    """Decomposed CSS box-shadow parameters used for interpolation."""

    offset_x: float = 0.0
    offset_y: float = 4.0
    blur: float = 8.0
    spread: float = 0.0
    color: QColor = field(default_factory=lambda: QColor(0, 0, 0, 80))
