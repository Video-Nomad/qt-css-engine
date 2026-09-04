# pyright: reportPrivateUsage=false
"""Shared fixtures for the benchmark suite.

Heavy workload mirrors the PR description:
- 458 rules
- 321 widgets
- 40 widgets animating five properties over 15 synthetic frames

All benchmarks run offscreen (QT_QPA_PLATFORM=offscreen) and use a
single QApplication instance.
"""

import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

# Must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.warning=false")

# Install a Qt message handler that silences the offscreen
# "This plugin does not support propagateSizeHints()" spam which is
# triggered by layout resizes during the benchmark hierarchy setup.
# The handler is installed at import time so it covers hierarchy
# creation as well as the timed sections. We keep output clean by
# suppressing that specific warning and not re-emitting other Qt
# messages during the benchmark run (they are not relevant to the
# baseline workload).
try:
    from qt_css_engine.qt_compat.QtCore import qInstallMessageHandler  # noqa: E402

    def _bench_message_handler(*args: object) -> None:  # type: ignore[no-untyped-def]
        try:
            msg = str(args[-1]) if args else ""
            if "propagateSizeHints" in msg:
                return
        except Exception:
            return
        # Suppress other Qt messages during benchmarks to keep output clean.
        return

    try:
        qInstallMessageHandler(_bench_message_handler)
    except Exception:
        pass
except Exception:
    pass

from qt_css_engine.css.parser import extract_rules  # noqa: E402
from qt_css_engine.qt_compat.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from qt_css_engine import TransitionEngine
    from qt_css_engine.css.model import StyleRule


# ---------------------------------------------------------------------------
# QApplication singleton
# ---------------------------------------------------------------------------


def get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    assert isinstance(app, QApplication)
    return app


# ---------------------------------------------------------------------------
# Heavy stylesheet — 458 rules
# ---------------------------------------------------------------------------


def build_heavy_stylesheet(*, num_anim: int = 40, total_rules: int = 458) -> str:
    """Return a deterministic stylesheet with *total_rules* rules.

    Layout per animated item (8 rules each → 320 for 40 items):
        .item-N
        .item-N.on
        .item-N:hover
        .item-N.on:hover
        .container .item-N
        .container .item-N.on
        .wrapper .container .item-N
        .wrapper .container .item-N.on

    Remaining rules are noise (tag / id / class / descendant) to reach 458.
    """
    parts: list[str] = []

    # Animated items — 8 rules each, 5 animating props per widget.
    # Use single-longhand props to avoid shorthand expansion:
    # background-color, color, min-width, max-width, font-size
    # (border-color and border-radius would expand to 4 longhands each → 11 anims)
    transition_decl = (
        "    transition: background-color 300ms ease, color 300ms ease, "
        "min-width 300ms ease, max-width 300ms ease, font-size 300ms ease;"
    )
    for i in range(num_anim):
        base = f".item-{i}"
        on = f".item-{i}.on"
        # 1) base with transition (5 props only, all single longhands)
        parts.append(
            f"{base} {{\n"
            f"    background-color: #444444;\n"
            f"    color: #ffffff;\n"
            f"    min-width: 80px;\n"
            f"    max-width: 80px;\n"
            f"    font-size: 12px;\n"
            f"    border-style: solid;\n"
            f"    border-width: 2px;\n"
            f"    border-color: #555555;\n"
            f"{transition_decl}\n"
            f"}}"
        )
        # 2) .on — changes exactly those 5 props
        parts.append(
            f"{on} {{\n"
            f"    background-color: #4d88ff;\n"
            f"    color: #000000;\n"
            f"    min-width: 140px;\n"
            f"    max-width: 140px;\n"
            f"    font-size: 16px;\n"
            f"}}"
        )
        # 3) :hover — also only those 5 (different values)
        parts.append(
            f"{base}:hover {{\n"
            f"    background-color: #666666;\n"
            f"    color: #cccccc;\n"
            f"    min-width: 100px;\n"
            f"    max-width: 100px;\n"
            f"    font-size: 14px;\n"
            f"}}"
        )
        # 4) .on:hover
        parts.append(f"{on}:hover {{\n    background-color: #3366cc;\n    font-size: 15px;\n}}")
        # 5) descendant static: .container .item-N (no transition, non-animating)
        parts.append(f".container .item-{i} {{\n    padding: 4px;\n}}")
        # 6) descendant static: .container .item-N.on
        parts.append(f".container .item-{i}.on {{\n    padding: 6px;\n}}")
        # 7) 3-level descendant static: .wrapper .container .item-N
        parts.append(f".wrapper .container .item-{i} {{\n    margin: 2px;\n}}")
        # 8) 3-level descendant static .on
        parts.append(f".wrapper .container .item-{i}.on {{\n    margin: 4px;\n}}")

    # At this point len(parts) == 320 for num_anim=40
    assert len(parts) == num_anim * 8

    # Noise rules to reach total_rules (138 for 40 anim)
    remaining = total_rules - len(parts)
    # Split remaining: ~100 class noise, ~20 id noise, rest descendant noise.
    # Class-noise rules carry a large border-top-left-radius so the resize-storm
    # benchmark exercises the on_resize clamp path: 40px always exceeds half the
    # label's small height, so clamping engages and re-snaps on every resize.
    noise_classes = min(100, remaining)
    for n in range(noise_classes):
        parts.append(
            f".noise-{n} {{\n    background-color: #2a2a2a;\n    color: #999999;\n    border: 1px solid #333333;\n    border-top-left-radius: 40px;\n}}"
        )
    remaining -= noise_classes
    noise_ids = min(20, remaining)
    for n in range(noise_ids):
        parts.append(f"#noise-id-{n} {{\n    background-color: #1a1a1a;\n    min-width: 60px;\n}}")
    remaining -= noise_ids
    # Descendant noise
    for n in range(remaining):
        parts.append(f".noise-container .noise-{n % 100} {{\n    padding: {2 + n % 4}px;\n}}")

    assert len(parts) == total_rules, f"{len(parts)} != {total_rules}"
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Widget hierarchy — 321 widgets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeavyHierarchy:
    root: QFrame
    inner: QFrame
    anim_widgets: list[QPushButton]
    all_widgets: list[QWidget]
    wrappers: list[QFrame]
    containers: list[QFrame]
    noise_widgets: list[QWidget]


@dataclass(frozen=True)
class EngineBundle:
    engine: TransitionEngine
    all_widgets: list[QWidget]
    anim_widgets: list[QPushButton]
    root: QFrame
    rules: list[StyleRule]
    noise_widgets: list[QWidget]


def create_heavy_hierarchy(
    *,
    num_anim: int = 40,
    total_widgets: int = 321,
) -> HeavyHierarchy:
    """Create an offscreen widget tree with *total_widgets* widgets.

    Caller is responsible for deleting root via qt_delete or deleteLater.
    """
    app = get_app()

    root = QFrame()
    root.setObjectName("bench-root")
    root.resize(800, 600)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    inner = QFrame()
    inner.setProperty("class", "bench-inner")
    inner_layout = QVBoxLayout(inner)
    inner_layout.setContentsMargins(4, 4, 4, 4)
    inner_layout.setSpacing(2)
    layout.addWidget(inner)

    anim_widgets: list[QPushButton] = []
    wrappers: list[QFrame] = []
    containers: list[QFrame] = []
    noise_widgets: list[QWidget] = []
    all_widgets: list[QWidget] = [root, inner]

    # Animated branch: wrapper -> container -> button (3 widgets per anim)
    for i in range(num_anim):
        wrapper = QFrame()
        wrapper.setProperty("class", "wrapper")
        wrapper.setObjectName(f"wrapper-{i}")
        w_layout = QVBoxLayout(wrapper)
        w_layout.setContentsMargins(0, 0, 0, 0)
        wrappers.append(wrapper)
        all_widgets.append(wrapper)

        container = QFrame()
        container.setProperty("class", "container")
        container.setObjectName(f"container-{i}")
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        containers.append(container)
        all_widgets.append(container)

        btn = QPushButton(f"Item {i}")
        btn.setProperty("class", f"item-{i}")
        btn.setObjectName(f"item-btn-{i}")
        anim_widgets.append(btn)
        all_widgets.append(btn)

        c_layout.addWidget(btn)
        w_layout.addWidget(container)
        inner_layout.addWidget(wrapper)

    # Noise branch: fill remaining widgets
    # We have used 2 + 40*3 = 122 widgets, need total_widgets - 122 more
    need = total_widgets - len(all_widgets)
    # Add noise containers + labels in pairs
    noise_containers: list[QFrame] = []
    for n in range(need):
        if n % 2 == 0 and len(all_widgets) < total_widgets - 1:
            # noise container
            nc = QFrame()
            nc.setProperty("class", "noise-container")
            nc.setObjectName(f"noise-container-{n}")
            nc_layout = QVBoxLayout(nc)
            nc_layout.setContentsMargins(0, 0, 0, 0)
            noise_containers.append(nc)
            all_widgets.append(nc)
            inner_layout.addWidget(nc)
            # add a label inside it
            lab = QLabel(f"noise {n}")
            lab.setProperty("class", f"noise-{n % 100}")
            lab.setObjectName(f"noise-id-{n % 20}" if n % 5 == 0 else "")
            all_widgets.append(lab)
            noise_widgets.append(lab)
            nc_layout.addWidget(lab)
        else:
            lab = QLabel(f"noise {n}")
            lab.setProperty("class", f"noise-{n % 100}")
            lab.setObjectName(f"noise-id-{n % 20}" if n % 7 == 0 else "")
            all_widgets.append(lab)
            noise_widgets.append(lab)
            inner_layout.addWidget(lab)
        if len(all_widgets) >= total_widgets:
            break

    # Trim if over (due to pair logic)
    all_widgets = all_widgets[:total_widgets]

    # Show offscreen (required for polish/style to work, but no visible window)
    root.show()
    app.processEvents()

    return HeavyHierarchy(
        root=root,
        inner=inner,
        anim_widgets=anim_widgets,
        all_widgets=all_widgets,
        wrappers=wrappers,
        containers=containers,
        noise_widgets=noise_widgets,
    )


def build_engine_and_widgets(
    *,
    num_anim: int = 40,
    total_widgets: int = 321,
    total_rules: int = 458,
) -> EngineBundle:
    """Convenience: build stylesheet, engine and widget tree together."""
    from qt_css_engine import TransitionEngine

    css = build_heavy_stylesheet(num_anim=num_anim, total_rules=total_rules)
    _, rules = extract_rules(css)
    assert len(rules) == total_rules, f"expected {total_rules} rules, got {len(rules)}"

    hierarchy = create_heavy_hierarchy(num_anim=num_anim, total_widgets=total_widgets)

    engine = TransitionEngine(rules, startup_delay_ms=0)
    # Install as event filter so class-change polish path is exercised
    get_app().installEventFilter(engine)

    return EngineBundle(
        engine=engine,
        all_widgets=hierarchy.all_widgets,
        anim_widgets=hierarchy.anim_widgets,
        root=hierarchy.root,
        rules=rules,
        noise_widgets=hierarchy.noise_widgets,
    )


# ---------------------------------------------------------------------------
# Write counting — counts QWidget.setStyleSheet calls
# ---------------------------------------------------------------------------


@contextmanager
def count_writes() -> Generator[dict[str, int]]:
    """Context manager that counts setStyleSheet calls globally.

    Yields a dict with key 'count' that is updated in place.
    """
    from qt_css_engine.qt_compat.QtWidgets import QWidget

    counter: dict[str, int] = {"count": 0}
    orig = QWidget.setStyleSheet

    def counting_setStyleSheet(self: QWidget, sheet: str) -> None:
        counter["count"] += 1
        orig(self, sheet)

    QWidget.setStyleSheet = counting_setStyleSheet  # type: ignore[method-assign,assignment]
    try:
        yield counter
    finally:
        QWidget.setStyleSheet = orig  # type: ignore[method-assign,assignment]


def drain_deferred_work(app: QApplication, engine: TransitionEngine) -> None:
    """Flush trigger-phase deferred work before timing animation frames.

    Class/hover triggers schedule singleShot(0) style flushes and polish-queue
    entries that would otherwise land inside the first timed frames, inflating
    both timings and write counts. Drain them so the frames measure
    steady-state ticks (animation completion callbacks at the final frame are
    real per-frame work and stay inside the timed section).
    """
    for _ in range(3):
        app.processEvents()
        try:
            engine._flush_polish_queue()
        except Exception:
            pass
    app.processEvents()
