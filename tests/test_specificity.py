# Specificity (a-b-c) + Type#id — Qt stylesheet-syntax parity.
#
# Qt follows CSS2: a = #ids, b = .classes + [attrs] + :pseudos, c = Type names.
# Sub-controls (::...) and universal `*` contribute nothing. Equal specificity
# falls back to source order; the engine additionally keeps its :pressed/:clicked
# priority as a tiebreak inside equal specificity.

from qt_css_engine import TransitionEngine
from qt_css_engine.css.model import StyleRule
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.matching.compiler import compile_segment, compute_specificity
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtWidgets import QApplication, QPushButton, QWidget


def make_engine(css: str) -> TransitionEngine:
    _, rules = extract_rules(css)
    return TransitionEngine(rules, startup_delay_ms=0)


def make_matcher(css: str) -> tuple[RuleMatcher, list[StyleRule]]:
    _, rules = extract_rules(css)
    return RuleMatcher(rules), rules


# ---------------------------------------------------------------------------
# Compiler: Type#id
# ---------------------------------------------------------------------------


def test_compile_type_id() -> None:
    seg = compile_segment("QPushButton#okButton")
    assert seg.obj_name == "okButton"
    assert seg.tag == "QPushButton"
    assert seg.classes == frozenset()


def test_compile_id_class_combos() -> None:
    assert compile_segment("#ok").obj_name == "ok"
    tag_id_cls = compile_segment("QPushButton#ok.primary")
    assert (tag_id_cls.obj_name, tag_id_cls.tag, tag_id_cls.classes) == ("ok", "QPushButton", frozenset({"primary"}))
    id_cls = compile_segment("#ok.primary")
    assert (id_cls.obj_name, id_cls.tag, id_cls.classes) == ("ok", None, frozenset({"primary"}))
    cls_id = compile_segment(".btn#ok")
    assert (cls_id.obj_name, cls_id.tag, cls_id.classes) == ("ok", None, frozenset({"btn"}))


def test_compile_type_id_with_attrs() -> None:
    seg = compile_segment('QPushButton#ok[flat="false"]')
    assert seg.obj_name == "ok"
    assert seg.tag == "QPushButton"
    assert len(seg.attrs) == 1


# ---------------------------------------------------------------------------
# Specificity units (Qt doc CSS2 table + engine cases)
# ---------------------------------------------------------------------------


def test_specificity_qt_doc_examples() -> None:
    assert compute_specificity("*", frozenset()) == (0, 0, 0)
    assert compute_specificity("QPushButton", frozenset()) == (0, 0, 1)
    assert compute_specificity("QDialog QPushButton", frozenset()) == (0, 0, 2)
    assert compute_specificity("#x34y", frozenset()) == (1, 0, 0)
    assert compute_specificity("QPushButton#okButton", frozenset()) == (1, 0, 1)
    assert compute_specificity(".btn.level", frozenset()) == (0, 2, 0)
    assert compute_specificity(".btn", frozenset({":hover"})) == (0, 2, 0)
    assert compute_specificity(".item", frozenset()) == (0, 1, 0)
    assert compute_specificity(".menu .item", frozenset()) == (0, 2, 0)
    assert compute_specificity("QPushButton[flat=false]", frozenset()) == (0, 1, 1)
    # Sub-control name is a pseudo-element: ignored, only the type + pseudo count.
    assert compute_specificity("QComboBox::drop-down", frozenset({":hover"})) == (0, 1, 1)


def test_parser_stores_specificity_and_order() -> None:
    _, rules = extract_rules(".btn { color: red; } QPushButton#ok { color: blue; }")
    assert rules[0].specificity == (0, 1, 0)
    assert rules[1].specificity == (1, 0, 1)
    assert rules[1].order > rules[0].order


# ---------------------------------------------------------------------------
# Matcher: Type#id
# ---------------------------------------------------------------------------


def test_matcher_type_id(_app: QApplication) -> None:
    matcher, rules = make_matcher("QPushButton#okButton { color: red; }")
    ok = QPushButton()
    ok.setObjectName("okButton")
    assert matcher.matches(ok, rules[0]) is True
    wrong_name = QPushButton()
    wrong_name.setObjectName("other")
    assert matcher.matches(wrong_name, rules[0]) is False
    # Same objectName but wrong type must not match.
    wrong_type = QWidget()
    wrong_type.setObjectName("okButton")
    assert matcher.matches(wrong_type, rules[0]) is False


def test_matcher_id_class_combo(_app: QApplication) -> None:
    matcher, rules = make_matcher("QPushButton#ok.primary { color: red; }")
    w = QPushButton()
    w.setObjectName("ok")
    w.setProperty("class", "primary")
    assert matcher.matches(w, rules[0]) is True
    w.setProperty("class", "other")
    assert matcher.matches(w, rules[0]) is False


def test_matcher_universal_matches_all(_app: QApplication) -> None:
    matcher, rules = make_matcher("* { color: red; }")
    assert matcher.matches(QWidget(), rules[0]) is True
    assert matcher.matches(QPushButton(), rules[0]) is True


# ---------------------------------------------------------------------------
# Cascade: specificity decides, order breaks ties
# ---------------------------------------------------------------------------


def test_id_beats_class_regardless_of_order(_app: QApplication) -> None:
    for css in (
        ".btn { background-color: red; } #myBtn { background-color: blue; }",
        "#myBtn { background-color: blue; } .btn { background-color: red; }",
    ):
        engine = make_engine(css)
        w = QWidget()
        w.setObjectName("myBtn")
        w.setProperty("class", "btn")
        state = engine.cascade.collect(w, engine.get_context(w))
        assert state.target_props["background-color"] == "blue"


def test_type_id_beats_lone_id(_app: QApplication) -> None:
    engine = make_engine("#ok { background-color: red; } QPushButton#ok { background-color: blue; }")
    w = QPushButton()
    w.setObjectName("ok")
    state = engine.cascade.collect(w, engine.get_context(w))
    assert state.target_props["background-color"] == "blue"


def test_descendant_beats_single_regardless_of_order(_app: QApplication) -> None:
    for css in (
        ".item { background-color: red; } .menu .item { background-color: green; }",
        ".menu .item { background-color: green; } .item { background-color: red; }",
    ):
        engine = make_engine(css)
        parent = QWidget()
        parent.setProperty("class", "menu")
        child = QWidget(parent)
        child.setProperty("class", "item")
        state = engine.cascade.collect(child, engine.get_context(child))
        assert state.target_props["background-color"] == "green"


def test_attr_beats_plain_class(_app: QApplication) -> None:
    engine = make_engine(".item { background-color: red; } .item[active=true] { background-color: green; }")
    w = QWidget()
    w.setProperty("class", "item")
    w.setProperty("active", True)
    state = engine.cascade.collect(w, engine.get_context(w))
    assert state.target_props["background-color"] == "green"


def test_equal_specificity_source_order_wins(_app: QApplication) -> None:
    engine = make_engine(".a { background-color: red; } .b { background-color: blue; }")
    w = QWidget()
    w.setProperty("class", "a b")
    state = engine.cascade.collect(w, engine.get_context(w))
    assert state.target_props["background-color"] == "blue"


def test_pressed_beats_hover_regardless_of_order(_app: QApplication) -> None:
    # Same specificity (0,2,0) each — engine priority keeps :pressed sticky.
    engine = make_engine("""
    .btn:pressed { background-color: red; transition: background-color 100ms; }
    .btn:hover { background-color: green; transition: background-color 100ms; }
    """)
    w = QWidget()
    w.setProperty("class", "btn")
    engine.get_context(w).active_pseudos = {":hover", ":pressed"}
    state = engine.cascade.collect(w, engine.get_context(w))
    assert state.target_props["background-color"] == "red"


def test_transitions_follow_specificity_winner(_app: QApplication) -> None:
    engine = make_engine("""
    .btn { background-color: red; transition: background-color 100ms; }
    #myBtn { background-color: blue; transition: background-color 500ms; }
    """)
    w = QWidget()
    w.setObjectName("myBtn")
    w.setProperty("class", "btn")
    state = engine.cascade.collect(w, engine.get_context(w))
    assert state.target_props["background-color"] == "blue"
    assert state.transitions["background-color"].duration_ms == 500
