"""Evaluation types — cause, collected rule state, and per-property resolution."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qt_css_engine.animation.factory import Animation
    from qt_css_engine.css.model import TransitionSpec


class EvaluationCause(Enum):
    """Why the engine is evaluating a widget's CSS state right now."""

    DIRECT = auto()
    POLISH = auto()
    PSEUDO_STATE = auto()
    CLASS_CHANGE = auto()
    CLASS_ANIMATION_FINISH = auto()
    CLICKED_ACTIVATION = auto()
    WINDOW_DEACTIVATE = auto()
    RULE_RELOAD = auto()
    DELAY_FIRE = auto()

    @property
    def snaps_transitions(self) -> bool:
        """True when animations should snap to target immediately rather than run.

        Only POLISH snaps — initial polish/layout must not animate from zero-size.
        All other causes (including RULE_RELOAD for border-radius) may animate or
        snap based on duration / enabled flag, not this property.
        """
        return self is EvaluationCause.POLISH

    @property
    def is_class_driven(self) -> bool:
        """True when the evaluation was triggered by a class property change."""
        return self is EvaluationCause.CLASS_CHANGE

    @property
    def is_clicked_driven(self) -> bool:
        """True when the evaluation was triggered by a :clicked pseudo activation."""
        return self is EvaluationCause.CLICKED_ACTIVATION


@dataclass
class ResolvedRuleState:
    """CSS values and transitions selected for one widget evaluation."""

    base_props: dict[str, str] = field(default_factory=dict)
    target_props: dict[str, str] = field(default_factory=dict)
    transitions: dict[str, TransitionSpec] = field(default_factory=dict)
    animated_props: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ResolvedProperty:
    """Values needed to decide how one CSS property should change."""

    animation: Animation | None
    current: str
    target: str
    is_natural_target: bool
    spec: TransitionSpec | None
