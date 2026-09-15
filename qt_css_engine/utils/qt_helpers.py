"""Qt helper utilities."""

import warnings
from collections.abc import Callable
from typing import Any


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
