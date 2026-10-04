"""Qt helper utilities."""

import warnings
import weakref
from collections.abc import Callable
from typing import Any, Concatenate

from qt_css_engine.qt_compat import is_qobject_alive
from qt_css_engine.qt_compat.QtWidgets import QWidget


def weak_widget_callback[**P](widget: QWidget, callback: Callable[Concatenate[QWidget, P], None]) -> Callable[P, None]:
    """Resolve the widget when called, without giving the callback ownership of it."""
    widget_ref = weakref.ref(widget)

    def invoke(*args: P.args, **kwargs: P.kwargs) -> None:
        target = widget_ref()
        if target is not None and is_qobject_alive(target):
            callback(target, *args, **kwargs)

    return invoke


def safe_disconnect(signal: Any, callback: Callable[..., Any] | None = None) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if callback is not None:
                signal.disconnect(callback)
            else:
                signal.disconnect()
    except RuntimeError, TypeError:
        pass
