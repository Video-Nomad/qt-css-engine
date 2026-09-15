"""Widget state composition — canonical per-widget state.

WidgetState owns cohesive sub-states (pseudo/anim/style/delay/geometry) plus the
internal-write guard. Flat attribute names are preserved as properties while new
code can use the composed ``pseudos/anims/style/delays/geometry`` objects directly.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qt_css_engine.animation.factory import Animation
    from qt_css_engine.qt_compat.QtCore import QTimer
    from qt_css_engine.state.suppress import InternalWriteReason


@dataclass
class PseudoState:
    active: set[str] = field(default_factory=set)


@dataclass
class AnimState:
    active: dict[str, Animation] = field(default_factory=dict)
    class_props: set[str] = field(default_factory=set)
    class_gen: int = 0
    class_callbacks: dict[str, Callable[[], None]] = field(default_factory=dict)
    clicked_props: set[str] = field(default_factory=set)
    clicked_gen: int = 0
    clicked_callbacks: dict[str, Callable[[], None]] = field(default_factory=dict)


@dataclass
class StyleState:
    css_props: dict[str, str] = field(default_factory=dict)
    box_props: dict[str, str] = field(default_factory=dict)
    applied_style: str | None = None
    flush_pending: bool = False
    flush_immediate: bool = False
    applied_cursor: str | None = None


@dataclass
class DelayState:
    pending: dict[str, QTimer] = field(default_factory=dict)


@dataclass
class GeometrySnap:
    pre_polish_size: tuple[int, int] | None = None


@dataclass
class WidgetState:
    """Composed per-widget state — canonical store value."""

    pseudos: PseudoState = field(default_factory=PseudoState)
    anims: AnimState = field(default_factory=AnimState)
    style: StyleState = field(default_factory=StyleState)
    delays: DelayState = field(default_factory=DelayState)
    geometry: GeometrySnap = field(default_factory=GeometrySnap)
    internal_write_depth: int = 0
    internal_write_reason: InternalWriteReason | None = None

    @property
    def active_pseudos(self) -> set[str]:
        return self.pseudos.active

    @active_pseudos.setter
    def active_pseudos(self, value: set[str]) -> None:
        self.pseudos.active = value

    @property
    def css_anim_props(self) -> dict[str, str]:
        return self.style.css_props

    @css_anim_props.setter
    def css_anim_props(self, value: dict[str, str]) -> None:
        self.style.css_props = value

    @property
    def active_animations(self) -> dict[str, Animation]:
        return self.anims.active

    @active_animations.setter
    def active_animations(self, value: dict[str, Animation]) -> None:
        self.anims.active = value

    @property
    def class_anim_props(self) -> set[str]:
        return self.anims.class_props

    @class_anim_props.setter
    def class_anim_props(self, value: set[str]) -> None:
        self.anims.class_props = value

    @property
    def class_anim_gen(self) -> int:
        return self.anims.class_gen

    @class_anim_gen.setter
    def class_anim_gen(self, value: int) -> None:
        self.anims.class_gen = value

    @property
    def class_anim_callbacks(self) -> dict[str, Callable[[], None]]:
        return self.anims.class_callbacks

    @class_anim_callbacks.setter
    def class_anim_callbacks(self, value: dict[str, Callable[[], None]]) -> None:
        self.anims.class_callbacks = value

    @property
    def clicked_anim_props(self) -> set[str]:
        return self.anims.clicked_props

    @clicked_anim_props.setter
    def clicked_anim_props(self, value: set[str]) -> None:
        self.anims.clicked_props = value

    @property
    def clicked_anim_gen(self) -> int:
        return self.anims.clicked_gen

    @clicked_anim_gen.setter
    def clicked_anim_gen(self, value: int) -> None:
        self.anims.clicked_gen = value

    @property
    def clicked_anim_callbacks(self) -> dict[str, Callable[[], None]]:
        return self.anims.clicked_callbacks

    @clicked_anim_callbacks.setter
    def clicked_anim_callbacks(self, value: dict[str, Callable[[], None]]) -> None:
        self.anims.clicked_callbacks = value

    @property
    def style_box_props(self) -> dict[str, str]:
        return self.style.box_props

    @style_box_props.setter
    def style_box_props(self, value: dict[str, str]) -> None:
        self.style.box_props = value

    @property
    def style_flush_pending(self) -> bool:
        return self.style.flush_pending

    @style_flush_pending.setter
    def style_flush_pending(self, value: bool) -> None:
        self.style.flush_pending = value

    @property
    def applied_style(self) -> str | None:
        return self.style.applied_style

    @applied_style.setter
    def applied_style(self, value: str | None) -> None:
        self.style.applied_style = value

    @property
    def style_flush_immediate(self) -> bool:
        return self.style.flush_immediate

    @style_flush_immediate.setter
    def style_flush_immediate(self, value: bool) -> None:
        self.style.flush_immediate = value

    @property
    def applied_cursor(self) -> str | None:
        return self.style.applied_cursor

    @applied_cursor.setter
    def applied_cursor(self, value: str | None) -> None:
        self.style.applied_cursor = value

    @property
    def pending_delays(self) -> dict[str, QTimer]:
        return self.delays.pending

    @pending_delays.setter
    def pending_delays(self, value: dict[str, QTimer]) -> None:
        self.delays.pending = value

    @property
    def pre_polish_size(self) -> tuple[int, int] | None:
        return self.geometry.pre_polish_size

    @pre_polish_size.setter
    def pre_polish_size(self, value: tuple[int, int] | None) -> None:
        self.geometry.pre_polish_size = value
