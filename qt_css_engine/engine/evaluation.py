"""Evaluation cause — why the engine is evaluating a widget's style."""

from enum import Enum, auto


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
