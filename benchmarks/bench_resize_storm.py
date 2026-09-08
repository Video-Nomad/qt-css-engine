# pyright: reportPrivateUsage=false
"""Resize storm — resize 100 radius-styled widgets through the on_resize clamp path."""

from benchmarks.common import build_engine_and_widgets, count_writes, get_app
from benchmarks.runner import BenchResult, summarize, time_call
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete

NAME = "Resize storm"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    bundle = build_engine_and_widgets()
    app = get_app()

    # Prime initial evaluation
    for w in bundle.all_widgets:
        try:
            bundle.engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
        except RuntimeError:
            pass
    app.processEvents()

    # Noise labels carry border-top-left-radius in the heavy stylesheet, so
    # on_resize takes the clamp path (queue + forced polish + snap + flush).
    # Without a radius rule on_resize early-returns and the storm would only
    # measure Qt layout cost — fail loudly instead of timing a no-op.
    assert bundle.engine.matcher.index.flags.has_border_radius
    resize_targets = bundle.noise_widgets[:100]
    assert len(resize_targets) == 100
    assert any(rule.has_border_radius_props for w in resize_targets for rule in bundle.engine.matcher.matching_rules(w))

    per_run_writes: list[int] = []
    with count_writes() as ctr:

        def one_run() -> None:
            before = ctr["count"]
            for w in resize_targets:
                try:
                    w.resize(w.width() + 1, w.height() + 1)
                    bundle.engine.on_resize(w)
                except RuntimeError:
                    pass
            app.processEvents()
            try:
                bundle.engine._flush_polish_queue()
            except Exception:
                pass
            app.processEvents()
            # Revert size to keep idempotent
            for w in resize_targets:
                try:
                    w.resize(max(10, w.width() - 1), max(10, w.height() - 1))
                except RuntimeError:
                    pass
            app.processEvents()
            per_run_writes.append(ctr["count"] - before)

        times = time_call(one_run, warmup=warmup, runs=runs)

    app.removeEventFilter(bundle.engine)
    qt_delete(bundle.root)
    app.processEvents()
    extra: dict[str, object] = {"resized": len(resize_targets), "writes_last_run": per_run_writes[-1]}
    return summarize(NAME, times, extra=extra)
