# pyright: reportPrivateUsage=false
"""Class change — toggling .on on 40 widgets (5 props each)."""

import time

from benchmarks.common import build_engine_and_widgets, count_writes, get_app
from benchmarks.runner import BenchResult, summarize
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete

NAME = "Class change"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    app = get_app()
    times: list[float] = []
    last_writes = 0

    for run_idx in range(warmup + runs):
        bundle = build_engine_and_widgets()
        # Prime — hot caches
        for w in bundle.all_widgets:
            try:
                bundle.engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
        app.processEvents()

        # Time a single class-change burst (40 widgets toggling .on)
        # Real path: setProperty posts DynamicPropertyChange, engine handles via eventFilter.
        if run_idx < warmup:
            for i, w in enumerate(bundle.anim_widgets):
                w.setProperty("class", f"item-{i} on")
            app.processEvents()
            try:
                bundle.engine._flush_polish_queue()
            except Exception:
                pass
            app.processEvents()
        else:
            with count_writes() as ctr:
                t0 = time.perf_counter()
                for i, w in enumerate(bundle.anim_widgets):
                    w.setProperty("class", f"item-{i} on")
                app.processEvents()
                try:
                    bundle.engine._flush_polish_queue()
                except Exception:
                    pass
                app.processEvents()
                t1 = time.perf_counter()
                times.append((t1 - t0) * 1000.0)
                last_writes = ctr["count"]

        app.removeEventFilter(bundle.engine)
        qt_delete(bundle.root)
        app.processEvents()

    extra: dict[str, object] = {"writes_last_run": last_writes, "anim_widgets": 40}
    return summarize(NAME, times, extra=extra)
