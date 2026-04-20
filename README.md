# Qt CSS Engine

A CSS animation engine for PyQt6/PySide6 that extends Qt's static stylesheet system (QSS) with dynamic CSS transitions and extra properties like `box-shadow`, `opacity` and CSS gradients. Qt's stylesheet engine has no concept of time or interpolation — this project implements an out-of-band animation system that intercepts `transition:` declarations from a stylesheet, tracks widget pseudo-states (hover, pressed, focus), and drives smooth property animations via Qt's animation framework. Dynamic class change is also supported so `.btn` -> `.btn.active` -> `.btn.other-state` will animate based on the `transition` property.

All that is required is to install `TransitionEngine` as an event filter and it will take care of the rest.

CSS Hot reload is supported via `TransitionEngine.reload_rules(new_rules)`

Subcontrols (e.g `::item`, `::handle`) are not supported because they are not real QWidgets. They will be just passed through as classic QSS stylesheet blocks.

This engine was made primarily for use in [YASB](https://github.com/amnweb/yasb) project and this will be the main focus for now, but it can be integrated into any Qt application.

**WARNING: This project is still in early development, very experimental and is not ready for production use. There will be bugs and breaking changes.**

## Python and Qt version

Python 3.14+

PySide6/PyQt6 6.10+

## Installation

```
uv add qt-css-engine
```

## Usage

```python
from qt_css_engine import TransitionEngine, extract_rules

app = QApplication([])
cleaned_qss, rules = extract_rules(stylesheet)
app.setStyleSheet(cleaned_qss)
engine = TransitionEngine(rules)
app.installEventFilter(engine)
```

## Simple transition examples

```css
/* A simple widget with a hover transition */
.btn {
    background-color: steelblue;
    color: white;
    border-radius: 4px;
    transition: background-color 300ms ease;
}

.btn:hover {
    background-color: royalblue;
}

/* A widget with a box-shadow transition */
#btn {
    background-color: steelblue;
    color: white;
    border-radius: 4px;
    box-shadow: 0px 0px 0px black;
    transition: box-shadow 300ms ease;
}

#btn:hover {
    box-shadow: 4px 4px 4px black;
}
```

## More complex examples

Check the `./demo/main.py` for hot reloading, dynamic class change and more.

## Transition syntax and supported values

### Shorthand

```css
transition: <property> <duration> [<easing>] [<delay>];
transition: <property> <duration> [<delay>] [<easing>];

/* multiple */
transition: background-color 300ms ease, border-radius 200ms linear 50ms;

/* all animatable properties */
transition: all 300ms ease-in-out;
```

### Longhands

```css
transition-property: background-color, border-radius;
transition-duration: 300ms, 200ms;
transition-timing-function: ease, linear;
transition-delay: 0ms, 50ms;
```

Longhands override the shorthand when both are declared in the same block. Values cycle per the CSS spec when list lengths differ.

### Time units

| Unit | Example |
| --- | --- |
| Milliseconds | `300ms` |
| Seconds | `0.3s` |

### Easing curves

| Value | Description |
| --- | --- |
| `linear` | Constant speed |
| `ease` | Slow in, slow out (default) |
| `ease-in` | Slow start |
| `ease-out` | Slow end |
| `ease-in-out` | Slow start and end |
| `cubic-bezier(x1, y1, x2, y2)` | Custom curve — values outside `[0, 1]` produce overshoot |
| `steps(n)` / `steps(n, jump-end\|jump-start\|jump-none\|jump-both)` | Discrete stepped animation |
| `step-start` / `step-end` | Aliases for `steps(1, jump-start)` / `steps(1, jump-end)` |

### Delay

Positive delay: animation starts after the delay elapses. The property is frozen at its current rendered value during the delay period.

Negative delay: animation starts immediately but offset `|delay|` ms into the timeline, as if it had already been running that long.

```css
transition: background 400ms ease 100ms;   /* 100ms positive delay */
transition: background 400ms ease -100ms;  /* starts 100ms in */
/* OR */
transition: background 400ms 100ms ease;
transition: background 400ms -100ms ease;
```

## Supported properties

Color values accepted everywhere a color is listed: named (`red`, `steelblue`, …), `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()`, `hsl()`, `hsla()`.

Numeric values accepted everywhere a length is listed: `<n>px`, `<n>pt`, `<n>em`.

| Property | Description | Supported values | Transition | Static |
| --- | --- | --- | --- | --- |
| `background-color` / `background` | Background color | color values; `linear-gradient()`, `radial-gradient()`, `conic-gradient()` (static only) | ✅ solid colors | ✅ |
| `color` | Text color | color values | ✅ | ✅ |
| `border-color` | Border color shorthand (→ 4 sides) | color values | ✅ | ✅ |
| `border-top-color`, `border-right-color`, `border-bottom-color`, `border-left-color` | Per-side border color | color values | ✅ | ✅ |
| `border-width` | Border width shorthand (→ 4 sides) | length values | ✅ | ✅ |
| `border-top-width`, `border-right-width`, `border-bottom-width`, `border-left-width` | Per-side border width | length values | ✅ | ✅ |
| `border-radius` | Border radius shorthand (→ 4 corners) | length values | ✅ | ✅ |
| `border-top-left-radius`, `border-top-right-radius`, `border-bottom-right-radius`, `border-bottom-left-radius` | Per-corner border radius | length values | ✅ | ✅ |
| `padding` | Padding shorthand (→ 4 sides) | length values | ✅ | ✅ |
| `padding-top`, `padding-right`, `padding-bottom`, `padding-left` | Per-side padding | length values | ✅ | ✅ |
| `margin` | Margin shorthand (→ 4 sides) | length values | ✅ | ✅ |
| `margin-top`, `margin-right`, `margin-bottom`, `margin-left` | Per-side margin | length values | ✅ | ✅ |
| `width`, `height` | Widget size | length values | ✅ | ✅ |
| `min-width`, `max-width`, `min-height`, `max-height` | Size constraints | length values | ✅ | ✅ |
| `font-size` | Font size | length values | ✅ | ✅ |
| `font-weight` | Font weight | `100`–`900` | ✅ | ✅ |
| `letter-spacing` | Letter spacing | length values | ✅ | ✅ |
| `word-spacing` | Word spacing | length values | ✅ | ✅ |
| `spacing` | Qt widget item spacing | length values | ✅ | ✅ |
| `opacity` | Widget opacity — not native QSS, applied via `QGraphicsOpacityEffect` | `0.0`–`1.0` | ✅ | ✅ |
| `box-shadow` | Drop shadow — not native QSS, applied via `QGraphicsDropShadowEffect`. No `inset`, `spread` is ignored. First shadow wins when multiple are declared. | `<x> <y> [blur] [spread] <color>` | ✅ | ✅ |
| `cursor` | Mouse cursor — Qt QSS ignores `cursor`, applied via `setCursor()` | `default`, `pointer`, `text`, `crosshair`, `wait`, `progress`, `help`, `move`, `grab`, `grabbing`, `copy`, `alias`, `not-allowed`, `no-drop`, `cell`, `all-scroll`, `n-resize`, `s-resize`, `e-resize`, `w-resize`, `ne-resize`, `nw-resize`, `se-resize`, `sw-resize`, `ns-resize`, `ew-resize`, `nesw-resize`, `nwse-resize`, `row-resize`, `col-resize`, `none` | ❌ | ✅ |

