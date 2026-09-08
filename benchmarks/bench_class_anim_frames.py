# pyright: reportPrivateUsage=false
"""Class-animation frames — 15 synthetic frames, 40 widgets × 5 props.

Batched flush: once per widget per frame (600 writes for 15×40). Trigger-phase
deferred work is drained before timing, so the section below measures
steady-state ticks.
"""

import time

from benchmarks.common import EngineBundle, build_engine_and_widgets, count_writes, drain_deferred_work, get_app
from benchmarks.runner import BenchResult, summarize
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QCoreApplication

NAME = "Class-animation frames"


def _advance_frames(bundle: EngineBundle, frames: int = 15) -> None:
    """Advance all active animations through *frames* synthetic ticks."""
    for f in range(1, frames + 1):
        progress_ms = int((f / frames) * 300)  # 300ms duration as in stylesheet
        for w in bundle.anim_widgets:
            ctx = bundle.engine.store.contexts.get(id(w))
            if ctx is None:
                continue
            for anim_obj in list(ctx.active_animations.values()):
                try:
                    if anim_obj.anim.state() == QAbstractAnimation.State.Running:
                        anim_obj.anim.setCurrentTime(min(progress_ms, anim_obj.anim.duration()))
                except RuntimeError:
                    pass
        # Batched flush is deferred via singleShot(0); process it
        QCoreApplication.processEvents()
        for w in bundle.anim_widgets:
            ctx = bundle.engine.store.contexts.get(id(w))
            if ctx is not None and ctx.style_flush_pending:
                try:
                    bundle.engine.writer.flush_scheduled(w, id(w))
                except RuntimeError:
                    pass
        QCoreApplication.processEvents()


def benchmark(*, warmup: int = 1, runs: int = 5) -> BenchResult:
    app = get_app()
    times: list[float] = []
    last_writes = 0
    last_anim_count = 0

    for run_idx in range(warmup + runs):
        bundle = build_engine_and_widgets()
        # Prime initial state
        for w in bundle.all_widgets:
            try:
                bundle.engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
        app.processEvents()

        # Trigger class change that starts 5 animations per widget
        for i, w in enumerate(bundle.anim_widgets):
            w.setProperty("class", f"item-{i} on")
        drain_deferred_work(app, bundle.engine)

        # Now time 15 frames
        if run_idx < warmup:
            _advance_frames(bundle, frames=15)
        else:
            with count_writes() as ctr:
                t0 = time.perf_counter()
                _advance_frames(bundle, frames=15)
                t1 = time.perf_counter()
                times.append((t1 - t0) * 1000.0)
                last_writes = ctr["count"]
                last_anim_count = len(bundle.anim_widgets)

        app.removeEventFilter(bundle.engine)
        qt_delete(bundle.root)
        app.processEvents()
        QCoreApplication.processEvents()

    extra: dict[str, object] = {
        "frames": 15,
        "anim_widgets": last_anim_count,
        "writes_last_run": last_writes,
    }
    return summarize(NAME, times, extra=extra)
