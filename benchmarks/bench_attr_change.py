# pyright: reportPrivateUsage=false
"""Attr change — toggling [active=true] on 40 widgets (5 props each).

Mirrors the class-change benchmark, but drives the transition through a
dynamic-property attribute selector instead of a class toggle. The 40
`.item-N[active=true]` rules change the same 5 animating props as `.on`,
so timings and write counts are directly comparable.
"""

import time

from benchmarks.common import (
    build_heavy_stylesheet,
    count_writes,
    create_heavy_hierarchy,
    get_app,
)
from benchmarks.runner import BenchResult, summarize
from qt_css_engine import TransitionEngine
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete

NAME = "Attr change"


def build_attr_stylesheet(*, num_anim: int = 40) -> str:
    """Heavy stylesheet plus one `[active=true]` rule per animated item."""
    return build_heavy_stylesheet(num_anim=num_anim, num_attr=num_anim)


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    app = get_app()
    times: list[float] = []
    last_writes = 0

    for run_idx in range(warmup + runs):
        css = build_attr_stylesheet()
        _, rules = extract_rules(css)
        hierarchy = create_heavy_hierarchy()
        engine = TransitionEngine(rules, startup_delay_ms=0)
        app.installEventFilter(engine)
        # Prime — hot caches
        for w in hierarchy.all_widgets:
            try:
                engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
        app.processEvents()

        # Time a single attr-change burst (40 widgets setting active=true).
        # Real path: setProperty posts DynamicPropertyChange, engine handles via eventFilter.
        if run_idx < warmup:
            for w in hierarchy.anim_widgets:
                w.setProperty("active", True)
            app.processEvents()
            try:
                engine._flush_polish_queue()
            except Exception:
                pass
            app.processEvents()
        else:
            with count_writes() as ctr:
                t0 = time.perf_counter()
                for w in hierarchy.anim_widgets:
                    w.setProperty("active", True)
                app.processEvents()
                try:
                    engine._flush_polish_queue()
                except Exception:
                    pass
                app.processEvents()
                t1 = time.perf_counter()
                times.append((t1 - t0) * 1000.0)
                last_writes = ctr["count"]

        app.removeEventFilter(engine)
        qt_delete(hierarchy.root)
        app.processEvents()

    extra: dict[str, object] = {"writes_last_run": last_writes, "anim_widgets": 40}
    return summarize(NAME, times, extra=extra)
