"""Scenario implementations with explicit timing boundaries and workload checks."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from benchmarks.common import (
    EngineBundle,
    build_heavy_stylesheet,
    create_heavy_hierarchy,
    drain_deferred_work,
    engine_bundle,
    get_app,
)
from benchmarks.runner import BenchResult, Workload, measure
from qt_css_engine import TransitionEngine
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPointF
from qt_css_engine.qt_compat.QtGui import QColor, QHoverEvent
from qt_css_engine.qt_compat.QtWidgets import QVBoxLayout

ANIM_PROPS = {"background-color", "color", "min-width", "max-width", "font-size"}
Trigger = Literal["class", "attr", "hover"]


def prime(bundle: EngineBundle) -> None:
    for widget in bundle.all_widgets:
        bundle.engine.on_polish(widget)
    drain_deferred_work(get_app(), bundle.engine)


def metadata(bundle: EngineBundle) -> dict[str, object]:
    return {"widgets": len(bundle.all_widgets), "rules": len(bundle.rules)}


def matching(name: str, *, cold: bool, attrs: bool, warmup: int, runs: int) -> BenchResult:
    @contextmanager
    def sample() -> Generator[Workload]:
        with engine_bundle(num_attr=40 if attrs else 0, active_every=2 if attrs else 0) as bundle:
            # No event filtering or processing in matcher microbenchmarks.
            get_app().removeEventFilter(bundle.engine)
            matcher = bundle.engine.matcher
            expected = [
                [rule for rule in matcher.rules if matcher.matches(widget, rule)] for widget in bundle.all_widgets
            ]
            matcher.clear_caches()
            if not cold:
                for widget in bundle.all_widgets:
                    matcher.matching_rules(widget)
            iterations = 1 if cold else 100
            actual: list[list[int]] = []

            def run() -> None:
                for _ in range(iterations):
                    for widget in bundle.all_widgets:
                        candidates = matcher.matching_rules(widget)
                        if attrs:
                            # The engine defers live attribute checks to the cascade.
                            for rule in candidates:
                                if rule.has_attrs:
                                    matcher.rule_attrs_match(widget, rule)

            def validate() -> None:
                for widget in bundle.all_widgets:
                    candidates = matcher.matching_rules(widget)
                    actual.append(
                        [
                            rule.order
                            for rule in candidates
                            if not rule.has_attrs or matcher.rule_attrs_match(widget, rule)
                        ]
                    )
                assert actual == [[rule.order for rule in rules] for rules in expected]
                if attrs:
                    assert sum(rule.has_attrs for rules in expected for rule in rules) == 20

            yield Workload(run, validate, metadata(bundle), iterations)

    return measure(name, sample, warmup=warmup, runs=runs)


def initial(name: str, *, attrs: bool, warmup: int, runs: int) -> BenchResult:
    # CSS parsing is excluded; index construction and the real Polish path are included.
    qss, rules = extract_rules(build_heavy_stylesheet(num_attr=40 if attrs else 0))

    @contextmanager
    def sample() -> Generator[Workload]:
        hierarchy = create_heavy_hierarchy(active_every=2 if attrs else 0, qss=qss)
        app = get_app()
        engines: list[TransitionEngine] = []

        def run() -> None:
            engine = TransitionEngine(rules, parent=hierarchy.root, startup_delay_ms=0)
            engines.append(engine)
            app.installEventFilter(engine)
            for widget in hierarchy.all_widgets:
                QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.Polish))
            drain_deferred_work(app, engine)

        def validate() -> None:
            engine = engines[0]
            for index, widget in enumerate(hierarchy.anim_widgets):
                ctx = engine.store.contexts[id(widget)]
                assert ANIM_PROPS <= ctx.css_anim_props.keys()
                assert widget.styleSheet()
                if attrs:
                    target = engine.cascade.collect(widget, ctx).target_props["background-color"]
                    assert target == ("#ff4444" if index % 2 == 0 else "#444444")

        try:
            yield Workload(run, validate, {"widgets": len(hierarchy.all_widgets), "rules": len(rules)})
        finally:
            for engine in engines:
                app.removeEventFilter(engine)
            qt_delete(hierarchy.root)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()

    return measure(name, sample, warmup=warmup, runs=runs, writes=True)


def trigger(bundle: EngineBundle, kind: Trigger) -> None:
    for index, widget in enumerate(bundle.anim_widgets):
        match kind:
            case "class":
                widget.setProperty("class", f"item-{index} on")
            case "attr":
                widget.setProperty("active", True)
            case "hover":
                event = QHoverEvent(QEvent.Type.HoverEnter, QPointF(1, 1), QPointF(1, 1), QPointF(-1, -1))
                QCoreApplication.sendEvent(widget, event)


def pause_animations(bundle: EngineBundle) -> list[QAbstractAnimation]:
    animations: list[QAbstractAnimation] = []
    for widget in bundle.anim_widgets:
        ctx = bundle.engine.store.contexts[id(widget)]
        running = {
            prop: obj.anim
            for prop, obj in ctx.active_animations.items()
            if obj.anim.state() == QAbstractAnimation.State.Running
        }
        assert running.keys() == ANIM_PROPS, f"Expected five running properties, got {list(running)}"
        for animation in running.values():
            assert animation.currentTime() == 0, "Real-time ticks contaminated setup"
            animation.pause()
            animations.append(animation)
    return animations


def change(name: str, *, kind: Trigger, warmup: int, runs: int) -> BenchResult:
    @contextmanager
    def sample() -> Generator[Workload]:
        with engine_bundle(num_attr=40 if kind == "attr" else 0) as bundle:
            prime(bundle)

            def run() -> None:
                # Qt delivers these events synchronously. No wall-clock frames
                # or paints are mixed into the event/transition-creation timing.
                trigger(bundle, kind)

            def validate() -> None:
                pause_animations(bundle)

            yield Workload(run, validate, {**metadata(bundle), "anim_widgets": 40, "animations": 200})

    return measure(name, sample, warmup=warmup, runs=runs, writes=True)


def frames(name: str, *, kind: Trigger, warmup: int, runs: int) -> BenchResult:
    @contextmanager
    def sample() -> Generator[Workload]:
        with engine_bundle() as bundle:
            prime(bundle)
            trigger(bundle, kind)
            animations = pause_animations(bundle)
            drain_deferred_work(get_app(), bundle.engine)
            assert all(
                animation.state() == QAbstractAnimation.State.Paused and animation.currentTime() == 0
                for animation in animations
            )

            def run() -> None:
                for frame in range(1, 16):
                    for animation in animations:
                        animation.setCurrentTime(frame * 20)
                    drain_deferred_work(get_app(), bundle.engine)

            def validate() -> None:
                assert len(animations) == 200
                assert all(animation.currentTime() == 300 for animation in animations)
                assert all(animation.state() == QAbstractAnimation.State.Stopped for animation in animations)
                for widget in bundle.anim_widgets:
                    ctx = bundle.engine.store.contexts[id(widget)]
                    assert not ctx.style_flush_pending
                    assert widget.styleSheet() == ctx.applied_style
                    target = bundle.engine.cascade.collect(widget, ctx).target_props
                    # Check all final values without relying on CSS number formatting.
                    for prop in ANIM_PROPS:
                        actual = ctx.css_anim_props[prop]
                        if prop.endswith("color"):
                            assert QColor(actual) == QColor(target[prop])
                        else:
                            assert float(actual.removesuffix("px")) == float(target[prop].removesuffix("px"))

            yield Workload(run, validate, {**metadata(bundle), "frames": 15, "anim_widgets": 40, "animations": 200})

    return measure(name, sample, warmup=warmup, runs=runs, writes=True)


def resize(name: str, *, warmup: int, runs: int) -> BenchResult:
    @contextmanager
    def sample() -> Generator[Workload]:
        with engine_bundle() as bundle:
            prime(bundle)
            # Freeze layout ownership so every target receives exactly one real
            # resize, unaffected by layout restoration or previous samples.
            for layout in bundle.root.findChildren(QVBoxLayout):
                layout.setEnabled(False)
            targets = bundle.noise_widgets[:100]
            assert len(targets) == 100
            for widget in targets:
                widget.resize(120, 30)
            drain_deferred_work(get_app(), bundle.engine)
            prop = "border-top-left-radius"
            for widget in targets:
                assert float(bundle.engine.store.contexts[id(widget)].css_anim_props[prop].removesuffix("px")) == 15

            def run() -> None:
                for widget in targets:
                    widget.resize(122, 34)
                drain_deferred_work(get_app(), bundle.engine)

            def validate() -> None:
                for widget in targets:
                    assert (widget.width(), widget.height()) == (122, 34)
                    ctx = bundle.engine.store.contexts[id(widget)]
                    assert float(ctx.css_anim_props[prop].removesuffix("px")) == 17
                    assert not ctx.style_flush_pending

            yield Workload(run, validate, {**metadata(bundle), "resized": len(targets)})

    return measure(name, sample, warmup=warmup, runs=runs, writes=True)
