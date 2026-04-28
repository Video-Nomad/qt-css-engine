import sys

import pytest

from qt_css_engine.qt_compat.QtWidgets import QApplication


@pytest.fixture(scope="session")
def _app() -> QApplication:  # type: ignore[reportUnusedFunction]
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication(sys.argv)
    assert isinstance(instance, QApplication)
    return instance
