"""Native Polish discovery, including local parsing and selector compilation."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from benchmarks.common import get_app
from benchmarks.runner import BenchResult, Workload, measure
from qt_css_engine import TransitionEngine, extract_rules
from qt_css_engine.matching.local import WidgetStyleMatcher
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QCoreApplication, Qt
from qt_css_engine.qt_compat.QtWidgets import QLabel, QWidget

type LocalMode = Literal["empty", "repeated", "unique"]


@contextmanager
def local_polish_workload(*, enabled: bool, mode: LocalMode) -> Generator[Workload]:
    app = get_app()
    qss, rules = extract_rules(".item { color: black; transition: color 300ms; } .item:hover { color: white; }")
    root = QWidget()
    root.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    root.setStyleSheet(qss)
    widgets = [QLabel(str(i), root) for i in range(320)]
    for i, widget in enumerate(widgets):
        widget.setProperty("class", "item" if i < 40 else "noise")
        if mode != "empty":
            padding = 2 if mode == "repeated" else i + 1
            widget.setStyleSheet(f"color: #333333; padding: {padding}px;")
    engine: TransitionEngine | None = None

    def run() -> None:
        nonlocal engine
        engine = TransitionEngine(rules, startup_delay_ms=0, collect_widget_styles=enabled)
        app.installEventFilter(engine)
        root.ensurePolished()
        for _ in range(3):
            QCoreApplication.processEvents()

    def validate() -> None:
        assert engine is not None
        assert len(root.findChildren(QLabel)) == 320
        assert len([w for w in widgets if engine.should_evaluate(w)]) == 40
        if enabled:
            assert isinstance(engine.matcher, WidgetStyleMatcher)
            expected = {"empty": 0, "repeated": 1, "unique": 320}[mode]
            # The bounded text cache can evict content still held by live sources.
            assert len(engine.matcher.sheet_cache) == min(expected, 128)
            assert len(engine.matcher.sources) == (1 if mode == "empty" else 321)
            assert engine.matcher.sources[id(root)].is_master
        for widget in widgets[:40]:
            ctx = engine.store.contexts[id(widget)]
            assert "color" in ctx.css_anim_props
            assert widget.palette().color(widget.foregroundRole()).name() == (
                "#000000" if mode == "empty" else "#333333"
            )

    try:
        yield Workload(run, validate, {"widgets": 321, "local_mode": mode, "collection_enabled": enabled})
    finally:
        if engine is not None:
            app.removeEventFilter(engine)
        qt_delete(root)
        if engine is not None:
            qt_delete(engine)
        for _ in range(3):
            QCoreApplication.processEvents()


def benchmark_disabled(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return measure(
        "Polish / collection disabled",
        lambda: local_polish_workload(enabled=False, mode="empty"),
        warmup=warmup,
        runs=runs,
        writes=True,
    )


def benchmark_empty(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return measure(
        "Polish / empty local sheets",
        lambda: local_polish_workload(enabled=True, mode="empty"),
        warmup=warmup,
        runs=runs,
        writes=True,
    )


def benchmark_repeated(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return measure(
        "Polish / repeated local sheet",
        lambda: local_polish_workload(enabled=True, mode="repeated"),
        warmup=warmup,
        runs=runs,
        writes=True,
    )


def benchmark_unique(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return measure(
        "Polish / unique local sheets",
        lambda: local_polish_workload(enabled=True, mode="unique"),
        warmup=warmup,
        runs=runs,
        writes=True,
    )
