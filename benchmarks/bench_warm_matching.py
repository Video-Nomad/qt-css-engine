# pyright: reportPrivateUsage=false
"""Warm cached matching — same 321 widgets after caches are hot."""

from benchmarks.common import build_engine_and_widgets, get_app
from benchmarks.runner import BenchResult, summarize, time_call
from qt_css_engine.qt_compat import qt_delete

NAME = "Warm cached matching"


def benchmark(*, warmup: int = 3, runs: int = 15) -> BenchResult:
    bundle = build_engine_and_widgets()
    matcher = bundle.engine.matcher
    app = get_app()

    # Prime caches once (cold)
    matcher.clear_caches()
    for w in bundle.all_widgets:
        matcher.matching_rules(w)
    app.processEvents()

    def one_run() -> None:
        for w in bundle.all_widgets:
            matcher.matching_rules(w)
        app.processEvents()

    times = time_call(one_run, warmup=warmup, runs=runs)

    extra: dict[str, object] = {"widgets": len(bundle.all_widgets), "rules": len(matcher.rules)}
    app.removeEventFilter(bundle.engine)
    qt_delete(bundle.root)
    app.processEvents()
    return summarize(NAME, times, extra=extra)
