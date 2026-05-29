import gc
import sys
from collections.abc import Generator
from typing import Any

import pytest

from qt_css_engine import TransitionEngine
from qt_css_engine.qt_compat.QtCore import QCoreApplication, QEvent
from qt_css_engine.qt_compat.QtWidgets import QApplication


@pytest.fixture(scope="session")
def _app() -> QApplication:  # type: ignore[reportUnusedFunction]
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication(sys.argv)
    assert isinstance(instance, QApplication)
    return instance


@pytest.fixture(autouse=True)
def cleanup_deferred_deletes(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """
    Prevents PySide6 test crashes by keeping the TransitionEngine alive while flushing
    pending DeferredDelete events. This avoids a race condition where Shiboken garbage
    collects the C++ parent engine before the event loop deletes its child animations.
    """

    engines_in_test: list[Any] = []
    old_init = TransitionEngine.__init__

    def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
        old_init(self, *args, **kwargs)
        engines_in_test.append(self)

    monkeypatch.setattr(TransitionEngine, "__init__", new_init)

    yield

    # Force processing of any pending DeferredDelete events while engines are still alive
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # Process other events
    QCoreApplication.processEvents()

    # Clear references to allow GC
    engines_in_test.clear()
    gc.collect()
