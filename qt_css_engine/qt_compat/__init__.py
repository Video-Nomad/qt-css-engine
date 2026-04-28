# pyright: reportMissingImports = false
# pyright: reportWildcardImportFromLibrary = false
# pyright: reportUnknownVariableType = false
# pyright: reportMissingTypeStubs = false
# ty: ignore[unresolved-import]
# ty: ignore[unused-ignore-comment]

from typing import Any

from ._api import USE_PYSIDE6


def qt_delete(obj: Any) -> None:
    """Synchronously delete a Qt C++ object, equivalent to C++ delete."""
    if USE_PYSIDE6:
        from shiboken6 import Shiboken

        Shiboken.delete(obj)  # type: ignore
    else:
        from PyQt6 import sip

        sip.delete(obj)  # type: ignore


def is_qobject_alive(obj: Any) -> bool:
    """True if the underlying C++ QObject still exists."""
    if obj is None:
        return False
    if USE_PYSIDE6:
        from shiboken6 import Shiboken

        return bool(Shiboken.isValid(obj))  # type: ignore
    from PyQt6 import sip

    return not sip.isdeleted(obj)  # type: ignore
