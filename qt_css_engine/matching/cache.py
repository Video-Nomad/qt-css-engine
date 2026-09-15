"""Caches for selector matching."""

import weakref
from itertools import islice
from typing import TYPE_CHECKING

from qt_css_engine.matching.compiler import WidgetIdentity

if TYPE_CHECKING:
    from qt_css_engine.css.model import StyleRule
    from qt_css_engine.qt_compat.QtWidgets import QWidget

CANDIDATE_CACHE_MAX = 2048


class IdentityCache:
    """Pure cache: WidgetIdentity -> candidate StyleRules (last-segment match only)."""

    def __init__(self) -> None:
        self._store: dict[WidgetIdentity, list[StyleRule]] = {}

    def get(self, ident: WidgetIdentity) -> list[StyleRule] | None:
        return self._store.get(ident)

    def set(self, ident: WidgetIdentity, candidates: list[StyleRule]) -> None:
        if len(self._store) >= CANDIDATE_CACHE_MAX:
            for stale in list(islice(self._store, CANDIDATE_CACHE_MAX // 4)):
                del self._store[stale]
        self._store[ident] = candidates

    def clear(self) -> None:
        self._store.clear()

    @property
    def store(self) -> dict[WidgetIdentity, list[StyleRule]]:
        return self._store


class WidgetCache:
    """Per-widget final rule list, keyed by id(widget) with weakref invalidation."""

    def __init__(self) -> None:
        self.rules: dict[int, list[StyleRule]] = {}
        self._refs: dict[int, weakref.ref[QWidget]] = {}
        self._idents: dict[int, WidgetIdentity] = {}

    def get(self, wid: int) -> list[StyleRule] | None:
        return self.rules.get(wid)

    def set(self, wid: int, widget: QWidget, ident: WidgetIdentity, rules: list[StyleRule]) -> None:
        self.rules[wid] = rules
        self._idents[wid] = ident
        self._refs[wid] = weakref.ref(widget, lambda _ref, _wid=wid: self.invalidate(_wid))

    def invalidate(self, wid: int) -> None:
        self.rules.pop(wid, None)
        self._refs.pop(wid, None)
        self._idents.pop(wid, None)

    def clear(self) -> None:
        self.rules.clear()
        self._refs.clear()
        self._idents.clear()

    def previous_ident(self, wid: int) -> WidgetIdentity | None:
        return self._idents.get(wid)

    @property
    def refs(self) -> dict[int, weakref.ref[QWidget]]:
        return self._refs

    @property
    def idents(self) -> dict[int, WidgetIdentity]:
        return self._idents
