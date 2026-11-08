"""Widget-local QSS captured through real native-platform Polish events."""

import gc
import weakref
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from qt_css_engine import TransitionEngine, extract_rules
from qt_css_engine.animation.color import ColorAnimation
from qt_css_engine.animation.numeric import GenericPropertyAnimation
from qt_css_engine.engine.evaluation import EvaluationCause
from qt_css_engine.geometry.natural_size import get_natural_size
from qt_css_engine.matching import local
from qt_css_engine.matching.local import WidgetStyleMatcher
from qt_css_engine.qt_compat import qt_delete
from qt_css_engine.qt_compat.QtCore import (
    QCoreApplication,
    QEvent,
    QMessageLogContext,
    Qt,
    QtMsgType,
    qInstallMessageHandler,
)
from qt_css_engine.qt_compat.QtGui import QColor, QPalette
from qt_css_engine.qt_compat.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def settle() -> None:
    for _ in range(3):
        QCoreApplication.processEvents()


@contextmanager
def qt_messages() -> Generator[list[str]]:
    messages: list[str] = []

    def record(_kind: QtMsgType, _context: QMessageLogContext, message: str | None) -> None:
        if message is not None:
            messages.append(message)

    previous = qInstallMessageHandler(record)
    try:
        yield messages
    finally:
        qInstallMessageHandler(previous)


@contextmanager
def installed_engine(
    app: QApplication, css: str = "", *, enabled: bool = True
) -> Generator[tuple[TransitionEngine, QWidget]]:
    _, rules = extract_rules(css)
    engine = TransitionEngine(rules, startup_delay_ms=0, collect_widget_styles=enabled)
    root = QWidget()
    root.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    app.installEventFilter(engine)
    try:
        yield engine, root
    finally:
        app.removeEventFilter(engine)
        qt_delete(root)
        qt_delete(engine)
        settle()


def polish(root: QWidget) -> None:
    root.ensurePolished()
    settle()


def hover(engine: TransitionEngine, widget: QWidget) -> ColorAnimation:
    engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))
    animation = engine.get_context(widget).active_animations["color"]
    assert isinstance(animation, ColorAnimation)
    animation.anim.pause()
    return animation


class CountingWidget(QWidget):
    style_reads: int = 0

    def styleSheet(self) -> str:
        self.style_reads += 1
        return super().styleSheet()


def test_collection_is_opt_in_and_disabled_path_does_not_read_qss(_app: QApplication) -> None:
    with installed_engine(_app, enabled=False) as (engine, root):
        widget = CountingWidget(root)
        qss = ".item { color: red; transition: color 100ms; }"
        widget.setStyleSheet(qss)
        widget.setProperty("class", "item")
        polish(root)
        assert widget.style_reads == 0
        assert not engine.should_evaluate(widget)
        assert QWidget.styleSheet(widget) == qss


def test_empty_sources_are_read_once_and_allocate_no_context(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = CountingWidget(root)
        polish(root)
        for _ in range(4):
            engine.on_polish(widget)
            assert not engine.should_evaluate(widget)
        assert widget.style_reads == 1
        assert id(widget) not in engine.store.contexts


def test_purely_static_local_sheet_is_not_rewritten_or_given_a_context(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = CountingWidget(root)
        qss = "color: red; padding: 4px;"
        widget.setStyleSheet(qss)
        polish(root)
        assert widget.style_reads == 1
        assert QWidget.styleSheet(widget) == qss
        assert id(widget) not in engine.store.contexts


def test_local_only_transition_is_discovered_before_master_filter(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QLabel("local", root)
        widget.setProperty("class", "item")
        widget.setStyleSheet("""
            .item { color: red; padding: 3px; transition: color 100ms linear; }
            .item:hover { color: blue; }
        """)
        polish(root)
        assert engine.should_evaluate(widget)
        assert widget.testAttribute(Qt.WidgetAttribute.WA_Hover)
        animation = hover(engine, widget)
        assert animation.start_color == QColor("red")
        assert animation.end_color == QColor("blue")
        animation.anim.setCurrentTime(50)
        engine.writer.flush_now(widget, engine.get_context(widget))
        assert widget.palette().color(QPalette.ColorRole.WindowText) == QColor(animation.current_color.name())
        animation.anim.setCurrentTime(100)
        assert widget.palette().color(QPalette.ColorRole.WindowText) == QColor("blue")
        assert "padding: 3px" in widget.styleSheet()


def test_construction_class_changes_do_not_capture_before_polish(_app: QApplication) -> None:
    master = ".item { color: red; transition: color 100ms; } .item:hover { color: blue; }"
    with installed_engine(_app, master) as (engine, root):
        widget = QWidget(root)
        widget.setProperty("class", "item")
        widget.setStyleSheet(".item { color: green; } .item:hover { color: yellow; }")
        polish(root)
        animation = hover(engine, widget)
        assert animation.start_color == QColor("green")
        assert animation.end_color == QColor("yellow")


def test_declaration_only_qss_provides_values_and_preserves_static_properties(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QLabel("bare", root)
        widget.setStyleSheet("color: red; padding: 4px; cursor: pointer; transition: color 100ms;")
        polish(root)
        state = engine.cascade.collect(widget, engine.get_context(widget))
        assert state.target_props["color"] == "red"
        assert state.transitions["color"].duration_ms == 100
        assert "padding: 4px" in widget.styleSheet()
        assert "transition:" not in widget.styleSheet()
        assert "cursor:" not in widget.styleSheet()
        assert widget.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_scope_and_source_precedence_beat_master_specificity(_app: QApplication) -> None:
    master = "#chosen { color: black; transition: color 900ms; } #chosen:hover { color: white; }"
    with installed_engine(_app, master) as (engine, root):
        panel = QWidget(root)
        panel.setStyleSheet("#chosen { color: red; transition: color 500ms; }")
        widget = QWidget(panel)
        widget.setObjectName("chosen")
        widget.setStyleSheet("* { color: green; transition: color 100ms; } *:hover { color: blue; }")
        sibling = QWidget(root)
        sibling.setObjectName("chosen")
        polish(root)
        animation = hover(engine, widget)
        assert animation.start_color == QColor("green")
        assert animation.end_color == QColor("blue")
        assert animation.anim.duration() == 100
        sibling_state = engine.cascade.collect(sibling, engine.get_context(sibling))
        assert sibling_state.target_props["color"] == "black"
        assert sibling_state.transitions["color"].duration_ms == 900


def test_container_sheet_affects_descendants_but_not_siblings(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        panel = QWidget(root)
        panel.setStyleSheet(".item { color: red; transition: color 100ms; } .item:hover { color: blue; }")
        widget = QWidget(panel)
        sibling = QWidget(root)
        for item in (widget, sibling):
            item.setProperty("class", "item")
        polish(root)
        assert not engine.should_evaluate(panel)
        assert engine.should_evaluate(widget)
        assert not engine.should_evaluate(sibling)
        assert "transition:" not in panel.styleSheet()
        assert hover(engine, widget).end_color == QColor("blue")
        assert engine.matcher.matching_rules(sibling) == []


def test_overlay_beats_authored_id_and_descendant_specificity(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        root.setObjectName("root")
        widget = QLabel("specific", root)
        widget.setObjectName("chosen")
        widget.setStyleSheet("""
            #root #chosen { color: red; transition: color 100ms linear; }
            #chosen:hover { color: blue; }
        """)
        polish(root)
        ctx = engine.get_context(widget)
        ctx.css_anim_props["color"] = "green"
        engine.writer.flush_now(widget, ctx)
        assert widget.palette().color(QPalette.ColorRole.WindowText) == QColor("green")
        assert '#root #chosen[_anim_scope="' in widget.styleSheet()
        assert ":hover[_anim_scope=" not in widget.styleSheet()


def test_generated_overlays_do_not_leak_into_descendants(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        panel = QWidget(root)
        panel.setStyleSheet("* { color: red; transition: color 100ms; } *:hover { color: blue; }")
        child = QLabel("child", panel)
        polish(root)
        animation = hover(engine, panel)
        animation.anim.setCurrentTime(100)
        assert child.palette().color(QPalette.ColorRole.WindowText) == QColor("red")


def test_identical_qss_shares_parsing_and_compilation(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widgets = [QWidget(root) for _ in range(12)]
        for widget in widgets:
            widget.setStyleSheet("color: red; transition: color 100ms;")
        with patch.object(local, "parse_local_stylesheet", wraps=local.parse_local_stylesheet) as parse:
            polish(root)
            assert parse.call_count == 1
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        sheets = [engine.matcher.sources[id(widget)] for widget in widgets]
        assert all(sheet is sheets[0] for sheet in sheets)
        for widget in widgets:
            engine.on_polish(widget)
        settle()
        assert len(engine.matcher.sheet_cache) == 1
        assert "_anim_scope" not in sheets[0].qss


def test_source_cache_is_bounded_without_dropping_live_bindings(
    _app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(local, "LOCAL_STYLESHEET_CACHE_MAX", 2)
    with installed_engine(_app) as (engine, root):
        widgets = [QWidget(root) for _ in range(3)]
        for i, widget in enumerate(widgets):
            widget.setStyleSheet(f"padding: {i + 1}px; transition: padding 100ms;")
        polish(root)
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert len(engine.matcher.sheet_cache) == 2
        for i, widget in enumerate(widgets):
            state = engine.cascade.collect(widget, engine.get_context(widget))
            assert state.target_props["padding-left"] == f"{i + 1}px"


def test_reparenting_refreshes_local_scope_for_entire_subtree(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        left = QWidget(root)
        right = QWidget(root)
        for panel, color in ((left, "red"), (right, "blue")):
            panel.setStyleSheet(f".item {{ color: {color}; transition: color 100ms; }}")
        branch = QWidget(left)
        child = QWidget(branch)
        for widget in (branch, child):
            widget.setProperty("class", "item")
        polish(root)
        assert engine.cascade.collect(child, engine.get_context(child)).target_props["color"] == "red"
        branch.setParent(right)
        settle()
        for widget in (branch, child):
            assert engine.cascade.collect(widget, engine.get_context(widget)).target_props["color"] == "blue"


def test_local_attribute_and_class_changes_invalidate_cached_rules(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QWidget(root)
        widget.setProperty("class", "item")
        widget.setStyleSheet("""
            .item { color: red; transition: color 100ms; }
            .item[active=true] { color: blue; }
            .other { color: green; transition: color 200ms; }
        """)
        polish(root)
        widget.setProperty("active", True)
        assert engine.cascade.collect(widget, engine.get_context(widget)).target_props["color"] == "blue"
        widget.setProperty("class", "other")
        state = engine.cascade.collect(widget, engine.get_context(widget))
        assert state.target_props["color"] == "green"
        assert state.transitions["color"].duration_ms == 200


def test_new_container_polish_invalidates_existing_descendant_scope(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        branch = QWidget(root)
        child = QWidget(branch)
        for widget in (branch, child):
            widget.setProperty("class", "item")
        polish(root)
        assert not engine.should_evaluate(child)

        panel = QWidget(root)
        panel.setStyleSheet(".item { color: blue; transition: color 100ms; }")
        branch.setParent(panel)
        polish(panel)
        for widget in (branch, child):
            assert engine.should_evaluate(widget)
            assert engine.cascade.collect(widget, engine.get_context(widget)).target_props["color"] == "blue"
            assert engine.get_context(widget).css_anim_props["color"] == "blue"


def test_local_transition_none_overrides_master_timing(_app: QApplication) -> None:
    master = ".item { color: red; transition: color 100ms; } .item:hover { color: blue; }"
    with installed_engine(_app, master) as (engine, root):
        widget = QLabel("snap", root)
        widget.setProperty("class", "item")
        widget.setStyleSheet(".item { transition: none; }")
        polish(root)
        engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))
        assert not engine.get_context(widget).active_animations
        assert widget.palette().color(QPalette.ColorRole.WindowText) == QColor("blue")


def test_reload_preserves_local_styles_and_transitions(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QLabel("reload", root)
        widget.setStyleSheet(
            "QLabel { color: red; padding: 5px; transition: color 100ms; } QLabel:hover { color: blue; }"
        )
        polish(root)
        hover(engine, widget).anim.setCurrentTime(100)
        _, rules = extract_rules("QLabel { color: green; }")
        engine.reload_rules(rules)
        settle()
        assert "padding: 5px" in widget.styleSheet()
        assert "transition:" not in widget.styleSheet()
        assert hover(engine, widget).end_color == QColor("blue")


def test_numeric_final_frame_and_measurement_preserve_authored_qss(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QLabel("measure", root)
        widget.resize(40, 20)
        widget.setStyleSheet("QLabel { color: red; transition: min-width 100ms; } QLabel:hover { min-width: 100px; }")
        polish(root)
        engine.eventFilter(widget, QEvent(QEvent.Type.HoverEnter))
        ctx = engine.get_context(widget)
        animation = ctx.active_animations["min-width"]
        assert isinstance(animation, GenericPropertyAnimation)
        animation.anim.pause()
        animation.anim.setCurrentTime(100)
        assert "color: red" in widget.styleSheet()
        assert widget.minimumWidth() == 100
        get_natural_size(widget, ctx, {}, "min-width", "100px")
        assert "color: red" in widget.styleSheet()


def test_master_sheet_on_widget_is_recognized_and_not_recascaded(_app: QApplication) -> None:
    css = ".item { color: red; transition: color 100ms; } .item:hover { color: blue; }"
    with installed_engine(_app, css) as (engine, root):
        qss, _ = extract_rules(css)
        root.setStyleSheet(qss)
        widget = QLabel("master", root)
        widget.setProperty("class", "item")
        polish(root)
        assert hover(engine, widget).end_color == QColor("blue")
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert engine.matcher.sources[id(root)].is_master
        assert all(rule.scope_rank == 0 for rule in engine.matcher.matching_rules(widget))


def test_master_root_qss_updates_on_reload(_app: QApplication) -> None:
    css = "QWidget { color: red; transition: color 100ms; } QWidget:hover { color: blue; }"
    with installed_engine(_app, css) as (engine, root):
        qss, _ = extract_rules(css)
        root.setStyleSheet(qss)
        polish(root)
        hover(engine, root).anim.setCurrentTime(100)
        new_qss, rules = extract_rules("QWidget { color: green; transition: color 100ms; }")
        engine.reload_rules(rules)
        root.setStyleSheet(new_qss)
        settle()
        assert "color: red" not in root.styleSheet()
        assert "color: green" in root.styleSheet()
        assert engine.cascade.collect(root, engine.get_context(root)).target_props["color"] == "green"


def test_source_bindings_do_not_keep_widgets_alive(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QWidget(root)
        widget.setStyleSheet("color: red; transition: color 100ms;")
        polish(root)
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        wid = id(widget)
        reference = weakref.ref(widget)
        widget.setParent(None)
        del widget
        gc.collect()
        assert reference() is None
        assert wid not in engine.matcher.sources
        assert wid not in engine.store.contexts


def test_later_stylesheet_replacements_are_outside_polish_snapshot(_app: QApplication) -> None:
    with installed_engine(_app) as (engine, root):
        widget = QWidget(root)
        widget.setStyleSheet("color: red; transition: color 100ms;")
        polish(root)
        widget.setStyleSheet("color: blue;")
        engine.on_polish(widget)
        state = engine.cascade.collect(widget, engine.get_context(widget))
        assert state.target_props["color"] == "red"


def test_manually_supplied_master_rules_still_support_local_sources(_app: QApplication) -> None:
    _, parsed = extract_rules(".item { transition: color 100ms; }")
    engine = TransitionEngine(list(parsed), collect_widget_styles=True, startup_delay_ms=0)
    widget = QWidget()
    try:
        widget.setProperty("class", "item")
        widget.setStyleSheet(".item { color: red; } .item:hover { color: blue; }")
        engine.on_polish(widget)
        engine.evaluate_widget_state(widget, cause=EvaluationCause.POLISH)
        assert hover(engine, widget).end_color == QColor("blue")
    finally:
        qt_delete(widget)
        qt_delete(engine)
        settle()


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_keeps_extensions_out_of_qt_during_construction_polish_and_render(
    _app: QApplication, enabled: bool
) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root), qt_messages() as messages:
        engine.set_widget_stylesheet(
            root,
            "QWidget { color: red; cursor: pointer; transition: color 100ms; } QWidget:hover { color: blue; }",
        )
        # Child construction can query the parent's rules before its first Polish.
        child = QLabel("child", root)
        engine.set_widget_stylesheet(
            child,
            "QLabel { color: green; transition: color 200ms; } QLabel:hover { color: yellow; }",
        )
        layout = QVBoxLayout(root)
        layout.addWidget(child)
        root.show()
        settle()
        assert not root.grab().isNull()
        assert "transition" not in root.styleSheet()
        assert "transition" not in child.styleSheet()
        assert "cursor:" not in root.styleSheet()
        animation = hover(engine, child)
        assert animation.end_color == QColor("yellow")
        assert animation.anim.duration() == 200
        animation.anim.setCurrentTime(200)
        assert not root.grab().isNull()
        assert not [message for message in messages if "Unknown property" in message]


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_shares_parse_cache_and_does_not_parse_cleaned_qss_on_polish(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root), qt_messages() as messages:
        root.show()
        settle()
        widgets = [QLabel("runtime", root) for _ in range(2)]
        qss = "QLabel { color: red; transition: color 100ms; } QLabel:hover { color: blue; }"
        with patch.object(local, "parse_local_stylesheet", wraps=local.parse_local_stylesheet) as parse:
            for widget in widgets:
                engine.set_widget_stylesheet(widget, qss)
                widget.show()
            settle()
            assert parse.call_count == 1
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert engine.matcher.sources[id(widgets[0])] is engine.matcher.sources[id(widgets[1])]
        assert len(engine.matcher.sheet_cache) == 1
        assert hover(engine, widgets[0]).end_color == QColor("blue")
        assert not [message for message in messages if "Unknown property" in message]


@pytest.mark.parametrize("enabled", [False, True])
def test_static_setter_does_not_allocate_context_or_reread_qss(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root):
        widget = CountingWidget(root)
        engine.set_widget_stylesheet(widget, "color: red; padding: 3px;")
        polish(root)
        assert widget.style_reads == 0
        assert id(widget) not in engine.store.contexts


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_replacement_stops_old_animation_and_keeps_current_pseudo_state(
    _app: QApplication, enabled: bool
) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root), qt_messages() as messages:
        widget = QLabel("replace", root)
        engine.set_widget_stylesheet(
            widget, "QLabel { color: red; transition: color 100ms; } QLabel:hover { color: blue; }"
        )
        polish(root)
        old = hover(engine, widget)
        old.anim.setCurrentTime(30)
        engine.set_widget_stylesheet(
            widget, "QLabel { color: green; transition: color 200ms; } QLabel:hover { color: yellow; }"
        )
        assert old.anim.state() == old.anim.State.Stopped
        settle()
        ctx = engine.get_context(widget)
        assert ":hover" in ctx.active_pseudos
        assert ctx.active_animations.get("color") is not old
        assert widget.palette().color(QPalette.ColorRole.WindowText) == QColor("yellow")
        assert "color: red" not in widget.styleSheet()
        assert not [message for message in messages if "Unknown property" in message]


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_replacement_invalidates_inherited_rules_for_cached_descendants(
    _app: QApplication, enabled: bool
) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root), qt_messages() as messages:
        panel = QWidget(root)
        engine.set_widget_stylesheet(panel, ".item { color: red; transition: color 100ms; }")
        branch = QWidget(panel)
        child = QLabel("descendant", branch)
        for widget in (branch, child):
            widget.setProperty("class", "item")
        polish(root)
        assert engine.cascade.collect(child, engine.get_context(child)).target_props["color"] == "red"
        engine.set_widget_stylesheet(panel, ".item { color: blue; transition: color 200ms; }")
        settle()
        for widget in (branch, child):
            state = engine.cascade.collect(widget, engine.get_context(widget))
            assert state.target_props["color"] == "blue"
            assert state.transitions["color"].duration_ms == 200
        assert not [message for message in messages if "Unknown property" in message]


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_clear_restores_master_styles(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, "QLabel { color: black; transition: color 100ms; }", enabled=enabled) as (engine, root):
        widget = QLabel("clear", root)
        engine.set_widget_stylesheet(widget, "color: red; transition: color 200ms;")
        polish(root)
        engine.set_widget_stylesheet(widget, "")
        settle()
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert id(widget) not in engine.matcher.sources
        assert engine.get_context(widget).style.local_sheet is None
        assert widget.palette().color(QPalette.ColorRole.WindowText) == QColor("black")


def test_setter_works_without_automatic_discovery(_app: QApplication) -> None:
    with installed_engine(_app, enabled=False) as (engine, root):
        engine.set_widget_stylesheet(
            root, "QWidget { color: red; transition: color 100ms; } QWidget:hover { color: blue; }"
        )
        polish(root)
        assert hover(engine, root).end_color == QColor("blue")
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert not engine.matcher.auto_discovery


@pytest.mark.parametrize("enabled", [False, True])
def test_prepared_sources_do_not_keep_unpolished_widgets_alive(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, _root):
        widget = QWidget()
        engine.set_widget_stylesheet(widget, "color: red; transition: color 100ms;")
        wid = id(widget)
        reference = weakref.ref(widget)
        del widget
        gc.collect()
        assert reference() is None
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert wid not in engine.matcher.sources
        assert wid not in engine.store.contexts


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_does_not_suppress_unrelated_qt_stylesheet_warnings(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root), qt_messages() as messages:
        widget = QLabel("warning", root)
        engine.set_widget_stylesheet(
            widget, "QLabel { color: red; invalid-demo-property: 1; transition: color 100ms; }"
        )
        polish(root)
        assert any("Unknown property invalid-demo-property" in message for message in messages)
        assert not any("Unknown property transition" in message for message in messages)


@pytest.mark.parametrize("enabled", [False, True])
def test_setter_clear_releases_engine_cursor(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root):
        widget = QLabel("cursor", root)
        engine.set_widget_stylesheet(widget, "cursor: pointer; color: red;")
        polish(root)
        assert widget.cursor().shape() == Qt.CursorShape.PointingHandCursor
        engine.set_widget_stylesheet(widget, "")
        settle()
        assert widget.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert engine.get_context(widget).applied_cursor is None


@pytest.mark.parametrize("own_style", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_setter_clear_releases_descendant_overlays_and_preserves_own_styles(
    _app: QApplication, own_style: bool, enabled: bool
) -> None:
    with (
        installed_engine(_app, "QLabel { color: black; }", enabled=enabled) as (engine, root),
        qt_messages() as messages,
    ):
        master_qss, _ = extract_rules("QLabel { color: black; }")
        root.setStyleSheet(master_qss)
        panel = QWidget(root)
        engine.set_widget_stylesheet(panel, "QLabel { color: red; cursor: pointer; transition: color 100ms; }")
        child = QLabel("clear inherited", panel)
        if own_style:
            engine.set_widget_stylesheet(child, "color: purple; padding: 3px;")
        polish(root)
        assert child.cursor().shape() == Qt.CursorShape.PointingHandCursor
        engine.set_widget_stylesheet(panel, "")
        settle()
        expected = QColor("purple" if own_style else "black")
        assert child.palette().color(QPalette.ColorRole.WindowText) == expected
        assert child.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert not engine.get_context(child).css_anim_props
        if own_style:
            assert "padding: 3px" in child.styleSheet()
        assert not [message for message in messages if "Unknown property" in message]


def test_setter_only_never_discovers_unregistered_widget_or_ancestor_sheets(_app: QApplication) -> None:
    with installed_engine(_app, enabled=False) as (engine, root):
        panel = CountingWidget(root)
        panel.setStyleSheet("color: white; padding: 3px;")
        untouched = [CountingWidget(root), CountingWidget(panel)]
        raw_qss = "color: purple; padding: 5px;"
        for widget in untouched:
            widget.setStyleSheet(raw_qss)
        target = CountingWidget(panel)
        target.setProperty("class", "selected")
        engine.set_widget_stylesheet(
            target, ".selected { color: red; transition: color 100ms; } .selected:hover { color: blue; }"
        )
        polish(root)
        for widget in (panel, *untouched):
            assert widget.style_reads == 0
            assert not engine.should_evaluate(widget)
        for widget in untouched:
            assert QWidget.styleSheet(widget) == raw_qss
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert set(engine.matcher.sources) == {id(target)}
        assert len(engine.matcher.sheet_cache) == 1
        assert hover(engine, target).end_color == QColor("blue")
        for widget in (panel, *untouched):
            assert widget.style_reads == 0


def test_first_setter_registration_preserves_running_master_animations(_app: QApplication) -> None:
    master = ".master { color: red; transition: color 200ms; } .master:hover { color: blue; }"
    with installed_engine(_app, master, enabled=False) as (engine, root):
        master_widget = QLabel("master", root)
        master_widget.setProperty("class", "master")
        polish(root)
        old_ctx = engine.get_context(master_widget)
        old_animation = hover(engine, master_widget)
        old_animation.anim.setCurrentTime(40)

        selected = QLabel("selected", root)
        engine.set_widget_stylesheet(
            selected, "QLabel { color: green; transition: color 100ms; } QLabel:hover { color: yellow; }"
        )
        selected.ensurePolished()
        settle()
        assert engine.get_context(master_widget) is old_ctx
        assert old_ctx.active_animations["color"] is old_animation
        assert old_animation.anim.currentTime() == 40
        assert engine.cascade.collect(master_widget, old_ctx).target_props["color"] == "blue"
        assert hover(engine, selected).end_color == QColor("yellow")

        # Master-only widgets retain their original gate before first Polish.
        later_master = QLabel("later master", root)
        later_master.setProperty("class", "master")
        assert engine.should_evaluate(later_master)
        assert engine.cascade.collect(later_master, engine.get_context(later_master)).target_props["color"] == "red"


def test_setter_only_registration_of_already_polished_container_updates_descendants(_app: QApplication) -> None:
    with installed_engine(_app, enabled=False) as (engine, root):
        panel = QWidget(root)
        branch = QWidget(panel)
        child = QLabel("already polished", branch)
        child.setProperty("class", "selected")
        polish(root)
        assert not engine.should_evaluate(child)
        engine.set_widget_stylesheet(
            panel, ".selected { color: red; transition: color 100ms; } .selected:hover { color: blue; }"
        )
        settle()
        assert engine.should_evaluate(child)
        assert child.testAttribute(Qt.WidgetAttribute.WA_Hover)
        assert hover(engine, child).end_color == QColor("blue")


def test_setter_only_container_preserves_unmanaged_descendant_qss(_app: QApplication) -> None:
    with installed_engine(_app, enabled=False) as (engine, root):
        panel = QWidget(root)
        untouched = CountingWidget(panel)
        raw_qss = "color: purple; padding: 5px;"
        untouched.setStyleSheet(raw_qss)
        polish(root)
        # Input events can allocate context even when the widget has no engine rules.
        engine.eventFilter(untouched, QEvent(QEvent.Type.HoverEnter))
        assert engine.get_context(untouched).applied_style is None
        engine.set_widget_stylesheet(panel, ".selected { color: red; transition: color 100ms; }")
        settle()
        engine.set_widget_stylesheet(panel, "")
        settle()
        assert QWidget.styleSheet(untouched) == raw_qss
        assert untouched.style_reads == 0


def test_setter_only_master_reload_keeps_registration_and_does_not_enable_discovery(_app: QApplication) -> None:
    with installed_engine(_app, enabled=False) as (engine, root):
        selected = QLabel("selected", root)
        engine.set_widget_stylesheet(
            selected, "QLabel { color: red; transition: color 100ms; } QLabel:hover { color: blue; }"
        )
        polish(root)
        hover(engine, selected).anim.setCurrentTime(100)
        _, rules = extract_rules("QLabel { color: green; }")
        engine.reload_rules(rules)
        settle()
        assert hover(engine, selected).end_color == QColor("blue")
        untouched = CountingWidget(root)
        untouched.setStyleSheet("color: purple;")
        untouched.ensurePolished()
        settle()
        assert untouched.style_reads == 0
        assert isinstance(engine.matcher, WidgetStyleMatcher)
        assert not engine.matcher.auto_discovery
        assert id(untouched) not in engine.matcher.sources


@pytest.mark.parametrize("enabled", [False, True])
def test_registered_container_reparenting_refreshes_descendant_scope(_app: QApplication, enabled: bool) -> None:
    with installed_engine(_app, enabled=enabled) as (engine, root):
        left = QWidget(root)
        right = QWidget(root)
        for panel, color in ((left, "red"), (right, "blue")):
            engine.set_widget_stylesheet(panel, f".selected {{ color: {color}; transition: color 100ms; }}")
        branch = QWidget(left)
        child = QLabel("move", branch)
        child.setProperty("class", "selected")
        polish(root)
        assert engine.cascade.collect(child, engine.get_context(child)).target_props["color"] == "red"
        branch.setParent(right)
        settle()
        assert engine.cascade.collect(child, engine.get_context(child)).target_props["color"] == "blue"
