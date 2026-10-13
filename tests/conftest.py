import sys
from collections.abc import Callable
from typing import Any

import pytest

from qt_css_engine.css.parser import extract_rules
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtWidgets import QApplication


@pytest.fixture(scope="session")
def _app() -> QApplication:  # type: ignore[reportUnusedFunction]
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication(sys.argv)
    assert isinstance(instance, QApplication)
    return instance


@pytest.fixture
def make_matcher() -> Callable[[str], Any]:

    def _make(css: str) -> RuleMatcher:
        _, rules = extract_rules(css)
        return RuleMatcher(rules)

    return _make
