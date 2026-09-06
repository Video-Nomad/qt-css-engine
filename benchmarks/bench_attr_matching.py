# pyright: reportPrivateUsage=false
"""Attr-selector matching and evaluation — cold, warm, initial.

Same 321-widget / 458-rule baseline workload plus 40
`.item-N[active=true]` rules (498 rules total). Half the animated widgets
carry `active=true`, so both the matching and non-matching attr branches
are exercised. Compare against the attr-free cold / warm / initial rows to
isolate the attribute-selector cost.
"""

from benchmarks.common import (
    build_heavy_stylesheet,
    create_heavy_hierarchy,
    get_app,
)
from benchmarks.runner import BenchResult, summarize, time_call
from qt_css_engine import TransitionEngine
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete

NAME_COLD = "Cold attr matching"
NAME_WARM = "Warm attr matching"
NAME_INITIAL = "Initial attr evaluation"

NUM_ATTR = 40


def _build_bundle():  # type: ignore[no-untyped-def]
    css = build_heavy_stylesheet(num_attr=NUM_ATTR)
    _, rules = extract_rules(css)
    hierarchy = create_heavy_hierarchy(active_every=2)
    engine = TransitionEngine(rules, startup_delay_ms=0)
    get_app().installEventFilter(engine)
    return engine, hierarchy


def benchmark_cold(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    engine, hierarchy = _build_bundle()
    matcher = engine.matcher
    app = get_app()
    assert len(matcher.rules) == 458 + NUM_ATTR
    assert len(matcher.tracked_attrs) == 1

    def one_run() -> None:
        matcher.clear_caches()
        for w in hierarchy.all_widgets:
            matcher.matching_rules(w)
        app.processEvents()

    times = time_call(one_run, warmup=warmup, runs=runs)

    extra: dict[str, object] = {"widgets": len(hierarchy.all_widgets), "rules": len(matcher.rules)}
    app.removeEventFilter(engine)
    qt_delete(hierarchy.root)
    app.processEvents()
    return summarize(NAME_COLD, times, extra=extra)


def benchmark_warm(*, warmup: int = 3, runs: int = 15) -> BenchResult:
    engine, hierarchy = _build_bundle()
    matcher = engine.matcher
    app = get_app()

    # Prime caches once (cold)
    matcher.clear_caches()
    for w in hierarchy.all_widgets:
        matcher.matching_rules(w)
    app.processEvents()

    def one_run() -> None:
        for w in hierarchy.all_widgets:
            matcher.matching_rules(w)
        app.processEvents()

    times = time_call(one_run, warmup=warmup, runs=runs)

    extra: dict[str, object] = {"widgets": len(hierarchy.all_widgets), "rules": len(matcher.rules)}
    app.removeEventFilter(engine)
    qt_delete(hierarchy.root)
    app.processEvents()
    return summarize(NAME_WARM, times, extra=extra)


def benchmark_initial(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    css = build_heavy_stylesheet(num_attr=NUM_ATTR)
    _, rules = extract_rules(css)
    hierarchy = create_heavy_hierarchy(active_every=2)
    app = get_app()

    def one_run() -> None:
        engine = TransitionEngine(rules, startup_delay_ms=0)
        for w in hierarchy.all_widgets:
            engine.ensure_wa_hover(w)
            engine.seed_active_pseudo(w)
        for w in hierarchy.all_widgets:
            try:
                engine.evaluate_widget_state(w, cause=EvaluationCause.POLISH)
            except RuntimeError:
                pass
        app.processEvents()
        engine.deleteLater()
        app.processEvents()

    times = time_call(one_run, warmup=warmup, runs=runs)

    qt_delete(hierarchy.root)
    app.processEvents()
    return summarize(NAME_INITIAL, times, extra={"widgets": len(hierarchy.all_widgets), "rules": len(rules)})


# Default entry (used if referenced as a single benchmark): cold.
NAME = NAME_COLD


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return benchmark_cold(warmup=warmup, runs=runs)
