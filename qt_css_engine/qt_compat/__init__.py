# pyright: reportMissingImports = false
# pyright: reportWildcardImportFromLibrary = false
# pyright: reportUnknownVariableType = false
# pyright: reportMissingTypeStubs = false
# ty: ignore[unresolved-import]
# ty: ignore[unused-ignore-comment]

from typing import Any

from ._api import USE_PYSIDE6

# Bound once at import. is_qobject_alive() runs on every animation tick of every animated
# property, and re-executing the import machinery there showed up on the hot path.
if USE_PYSIDE6:
    from shiboken6 import Shiboken as _binding  # type: ignore

    _delete = _binding.delete  # type: ignore
    _is_alive = _binding.isValid  # type: ignore
else:
    from PyQt6 import sip as _binding  # type: ignore

    _delete = _binding.delete  # type: ignore

    def _is_alive(obj: Any) -> bool:
        return not _binding.isdeleted(obj)  # type: ignore


def qt_delete(obj: Any) -> None:
    """Synchronously delete a Qt C++ object, equivalent to C++ delete."""
    _delete(obj)


def is_qobject_alive(obj: Any) -> bool:
    """True if the underlying C++ QObject still exists."""
    if obj is None:
        return False
    return bool(_is_alive(obj))
