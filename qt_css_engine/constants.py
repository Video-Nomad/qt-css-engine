# CSS / engine lookup tables — shared by css_parser and engine.
# Pure data: no logic, minimal imports.

from .qt_compat.QtCore import QEasingCurve, QEvent, Qt

# ---------------------------------------------------------------------------
# CSS property normalisation
# ---------------------------------------------------------------------------

# Canonical property name for aliased shorthand forms.
PROP_ALIASES: dict[str, str] = {"background": "background-color"}

# Shorthand properties that expand to four longhands (top, right, bottom, left order).
SHORTHAND_SIDES: dict[str, list[str]] = {
    "padding": ["padding-top", "padding-right", "padding-bottom", "padding-left"],
    "margin": ["margin-top", "margin-right", "margin-bottom", "margin-left"],
    "border-width": ["border-top-width", "border-right-width", "border-bottom-width", "border-left-width"],
    "border-color": ["border-top-color", "border-right-color", "border-bottom-color", "border-left-color"],
    # border-radius order: top-left, top-right, bottom-right, bottom-left
    "border-radius": [
        "border-top-left-radius",
        "border-top-right-radius",
        "border-bottom-right-radius",
        "border-bottom-left-radius",
    ],
}

BORDER_STYLE_KEYWORDS: frozenset[str] = frozenset(
    {"none", "hidden", "dotted", "dashed", "solid", "double", "groove", "ridge", "inset", "outset"}
)
BORDER_WIDTH_KEYWORDS: frozenset[str] = frozenset({"thin", "medium", "thick"})

# ---------------------------------------------------------------------------
# CSS pseudo-class tables
# ---------------------------------------------------------------------------

# Qt QSS pseudo-classes that map to a canonical pseudo-class tracked by the engine.
PSEUDO_ALIASES: dict[str, str] = {}

# Animation pseudo-classes the engine knows about, in descending priority order.
ANIMATION_PSEUDOS: frozenset[str] = frozenset({":pressed", ":hover", ":focus", ":checked", ":clicked"})
ANIMATION_PSEUDO_PRIORITY: tuple[str, ...] = (":clicked", ":pressed", ":hover", ":focus", ":checked")

# ---------------------------------------------------------------------------
# Engine property sets
# ---------------------------------------------------------------------------

# Properties handled via QGraphicsEffect regardless of whether a transition is defined.
EFFECT_PROPS: frozenset[str] = frozenset({"opacity", "box-shadow"})

# Size properties that fall back to widget.sizeHint() when no explicit CSS value exists.
SIZE_PROPS: frozenset[str] = frozenset({"width", "height", "min-width", "max-width", "min-height", "max-height"})

# Qt events that can trigger a pseudo-state change.
PSEUDO_EVENTS: frozenset[QEvent.Type] = frozenset(
    {
        QEvent.Type.HoverEnter,
        QEvent.Type.HoverLeave,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.FocusIn,
        QEvent.Type.FocusOut,
    }
)

EASING_MAP: dict[str, QEasingCurve.Type] = {
    "linear": QEasingCurve.Type.Linear,
    "ease": QEasingCurve.Type.InOutQuad,
    "ease-in": QEasingCurve.Type.InCubic,
    "ease-out": QEasingCurve.Type.OutCubic,
    "ease-in-out": QEasingCurve.Type.InOutCubic,
}

SUPPORTED_NUMERIC_PROPS: frozenset[str] = frozenset(
    {
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "width",
        "height",
        "border-width",
        "border-top-width",
        "border-right-width",
        "border-bottom-width",
        "border-left-width",
        "border-radius",
        "border-top-left-radius",
        "border-top-right-radius",
        "border-bottom-left-radius",
        "border-bottom-right-radius",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "font-size",
        "font-weight",
        "letter-spacing",
        "word-spacing",
        "spacing",
        "bottom",
        "left",
        "right",
        "top",
    }
)

# Props where Qt rejects negative values (cubic-bezier overshoot can produce them).
# Clamped to >= 0 when writing to the stylesheet; current_val is left unclamped so
# the animation trajectory is unaffected.
NON_NEGATIVE_PROPS: frozenset[str] = frozenset(
    {
        "width",
        "height",
        "min-width",
        "min-height",
        "max-width",
        "max-height",
        "border-width",
        "border-top-width",
        "border-right-width",
        "border-bottom-width",
        "border-left-width",
        "border-radius",
        "border-top-left-radius",
        "border-top-right-radius",
        "border-bottom-left-radius",
        "border-bottom-right-radius",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "font-size",
        "font-weight",
        "spacing",
    }
)

# ---------------------------------------------------------------------------
# Cursor map
# ---------------------------------------------------------------------------

# CSS cursor values → Qt cursor shapes.
# Omitted (no Qt equivalent): auto, url(), context-menu, vertical-text, zoom-in, zoom-out.
CURSOR_MAP: dict[str, Qt.CursorShape] = {
    # Basic
    "default": Qt.CursorShape.ArrowCursor,
    "none": Qt.CursorShape.BlankCursor,
    "pointer": Qt.CursorShape.PointingHandCursor,
    "crosshair": Qt.CursorShape.CrossCursor,
    "text": Qt.CursorShape.IBeamCursor,
    "wait": Qt.CursorShape.WaitCursor,
    "progress": Qt.CursorShape.BusyCursor,
    "help": Qt.CursorShape.WhatsThisCursor,
    "move": Qt.CursorShape.SizeAllCursor,
    "all-scroll": Qt.CursorShape.SizeAllCursor,
    "cell": Qt.CursorShape.CrossCursor,
    # Resize — cardinal and diagonal
    "n-resize": Qt.CursorShape.SizeVerCursor,
    "s-resize": Qt.CursorShape.SizeVerCursor,
    "ns-resize": Qt.CursorShape.SizeVerCursor,
    "e-resize": Qt.CursorShape.SizeHorCursor,
    "w-resize": Qt.CursorShape.SizeHorCursor,
    "ew-resize": Qt.CursorShape.SizeHorCursor,
    "ne-resize": Qt.CursorShape.SizeBDiagCursor,  # / diagonal (NE–SW)
    "sw-resize": Qt.CursorShape.SizeBDiagCursor,
    "nesw-resize": Qt.CursorShape.SizeBDiagCursor,
    "nw-resize": Qt.CursorShape.SizeFDiagCursor,  # \ diagonal (NW–SE)
    "se-resize": Qt.CursorShape.SizeFDiagCursor,
    "nwse-resize": Qt.CursorShape.SizeFDiagCursor,
    # Split (between rows/columns)
    "row-resize": Qt.CursorShape.SplitVCursor,
    "col-resize": Qt.CursorShape.SplitHCursor,
    # Drag
    "grab": Qt.CursorShape.OpenHandCursor,
    "grabbing": Qt.CursorShape.ClosedHandCursor,
    "copy": Qt.CursorShape.DragCopyCursor,
    "alias": Qt.CursorShape.DragLinkCursor,
    # Forbidden
    "not-allowed": Qt.CursorShape.ForbiddenCursor,
    "no-drop": Qt.CursorShape.ForbiddenCursor,
}
