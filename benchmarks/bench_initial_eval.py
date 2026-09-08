# pyright: reportPrivateUsage=false
"""Initial evaluation — polish burst for all 321 widgets.

Note: engine construction (rule-index build) is inside the timed section, so
this covers cold-start cost end to end. The widget tree is reused across runs,
so second and later runs are warm-ish (WA_Hover / scope state persists).
"""

from benchmarks.common import build_heavy_stylesheet, create_heavy_hierarchy, get_app
from benchmarks.runner import BenchResult, summarize, time_call
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.qt_compat import qt_delete

NAME = "Initial evaluation"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    from qt_css_engine import TransitionEngine

    css = build_heavy_stylesheet()
    _, rules = extract_rules(css)

    hierarchy = create_heavy_hierarchy()
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
    return summarize(NAME, times, extra={"widgets": len(hierarchy.all_widgets), "rules": len(rules)})
