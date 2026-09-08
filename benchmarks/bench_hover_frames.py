# pyright: reportPrivateUsage=false
"""Hover-animation frames — 15 synthetic hover frames, same 40×5 workload.

Note: hover is injected by toggling :hover in the widget context directly
instead of delivering real QHoverEvents (awkward offscreen), so this measures
evaluation + animation ticks, not event routing. Trigger-phase deferred work
is drained before timing (see drain_deferred_work).
"""

import time

from benchmarks.common import EngineBundle, build_engine_and_widgets, count_writes, drain_deferred_work, get_app
from benchmarks.runner import BenchResult, summarize
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QCoreApplication

NAME = "Hover-animation frames"


def _advance_frames(bundle: EngineBundle, frames: int = 15) -> None:
    for f in range(1, frames + 1):
        progress_ms = int((f / frames) * 300)
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

    for run_idx in range(warmup + runs):
        bundle = build_engine_and_widgets()
        for w in bundle.all_widgets:
            try:
                bundle.engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
        app.processEvents()

        # Trigger hover on all anim widgets
        for w in bundle.anim_widgets:
            ctx = bundle.engine.get_context(w)
            ctx.active_pseudos.add(":hover")
            try:
                bundle.engine.evaluate_widget_state(w, cause=EvaluationCause.PSEUDO_STATE)
            except RuntimeError:
                pass
        drain_deferred_work(app, bundle.engine)

        if run_idx < warmup:
            _advance_frames(bundle, frames=15)
        else:
            with count_writes() as ctr:
                t0 = time.perf_counter()
                _advance_frames(bundle, frames=15)
                t1 = time.perf_counter()
                times.append((t1 - t0) * 1000.0)
                last_writes = ctr["count"]

        app.removeEventFilter(bundle.engine)
        qt_delete(bundle.root)
        app.processEvents()
        QCoreApplication.processEvents()

    extra: dict[str, object] = {"frames": 15, "anim_widgets": 40, "writes_last_run": last_writes}
    return summarize(NAME, times, extra=extra)
