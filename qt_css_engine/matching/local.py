"""Polish-time widget stylesheet discovery and scoped rule matching."""

import weakref
from collections import OrderedDict
from dataclasses import dataclass, replace

import tinycss2
from tinycss2.ast import LiteralToken

from qt_css_engine.css.model import ParsedStyleRules, StyleRule
from qt_css_engine.css.parser import extract_rules
from qt_css_engine.matching.cache import WidgetCache
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtCore import Qt
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState

LOCAL_STYLESHEET_CACHE_MAX = 128


@dataclass(frozen=True)
class LocalStyleSheet:
    """Reusable parsed content; ownership and cascade rank belong to the widget."""

    qss: str
    matcher: RuleMatcher
    overlay_selectors: tuple[tuple[str, str], ...]
    needs_cleaning: bool
    is_master: bool = False


def overlay_selectors(rules: list[StyleRule]) -> tuple[tuple[str, str], ...]:
    overlays: list[tuple[str, str]] = []
    for rule in rules:
        if rule.subcontrol:
            continue
        # Insert the scope attribute before the final compound selector's pseudos.
        # Copying each authored selector with one extra attribute makes the overlay
        # outrank even ID/descendant rules without changing the widget's objectName.
        tokens = tinycss2.parse_component_value_list(rule.selector)
        start = 0
        for i, token in enumerate(tokens):
            if token.type == "whitespace" or (isinstance(token, LiteralToken) and token.value in (">", "+", "~")):
                start = i + 1
        insert = next(
            (
                i
                for i, token in enumerate(tokens)
                if i >= start and isinstance(token, LiteralToken) and token.value == ":"
            ),
            len(tokens),
        )
        overlays.append((tinycss2.serialize(tokens[:insert]), tinycss2.serialize(tokens[insert:])))
    return tuple(dict.fromkeys(overlays))


def parse_local_stylesheet(text: str) -> LocalStyleSheet:
    """Accept both complete QSS rules and Qt's declaration-only shorthand."""
    nodes = tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True)
    if not any(node.type == "qualified-rule" for node in nodes) and any(
        node.type == "declaration"
        for node in tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True)
    ):
        text = "* {" + text + "}"
    qss, rules = extract_rules(text)
    needs_cleaning = (
        any(
            rule.transitions or rule.resets_transitions or rule.has_effect_props or rule.has_cursor_prop
            for rule in rules
        )
        or "-gradient(" in text.lower()
    )
    return LocalStyleSheet(qss, RuleMatcher(rules), overlay_selectors(rules), needs_cleaning)


class WidgetStyleMatcher(RuleMatcher):
    """Master matcher plus cached sheets belonging to widgets and their ancestors.

    Automatic sources are captured once; explicit source bindings can be replaced.
    Warm matching uses a widget cache, with parsing/compilation shared by exact text.
    """

    def __init__(self, rules: list[StyleRule], *, auto_discovery: bool = True) -> None:
        self.auto_discovery = auto_discovery
        self.sheet_cache: OrderedDict[str, LocalStyleSheet] = OrderedDict()
        self.sources: dict[int, LocalStyleSheet] = {}
        self._refs: dict[int, weakref.ref[QWidget]] = {}
        self._captured: set[int] = set()
        self._prepared: set[int] = set()
        self._chains: dict[int, tuple[LocalStyleSheet, ...]] = {}
        self._scoped_cache = WidgetCache()
        self._master_sheet: LocalStyleSheet | None = None
        super().__init__(rules)

    def _observe(self, widget: QWidget) -> None:
        wid = id(widget)
        if wid not in self._refs:
            self._refs[wid] = weakref.ref(widget, lambda _ref, _wid=wid: self.forget_widget_id(_wid))

    def _parse_cached(self, text: str) -> LocalStyleSheet:
        if self._master_sheet is not None and text == self._master_sheet.qss:
            return self._master_sheet
        sheet = self.sheet_cache.get(text)
        if sheet is None:
            sheet = parse_local_stylesheet(text)
            self.sheet_cache[text] = sheet
            if len(self.sheet_cache) > LOCAL_STYLESHEET_CACHE_MAX:
                self.sheet_cache.popitem(last=False)
        else:
            self.sheet_cache.move_to_end(text)
        return sheet

    def _invalidate_scope(self, owner: QWidget) -> list[QWidget]:
        had_cached_scope = id(owner) in self._chains
        self.invalidate_widget_id(id(owner))
        affected: list[QWidget] = []
        if had_cached_scope:
            for cached_wid, widget_ref in list(self._refs.items()):
                target = widget_ref()
                if target is not None:
                    try:
                        if self._is_descendant_of(target, owner):
                            self.invalidate_widget_id(cached_wid)
                            if not self.auto_discovery or cached_wid in self._captured:
                                affected.append(target)
                    except RuntimeError:
                        self.forget_widget_id(cached_wid)
        return affected

    def set_source(self, widget: QWidget, text: str) -> tuple[LocalStyleSheet | None, list[QWidget]]:
        """Register authored CSS before its cleaned companion is passed to Qt."""
        sheet = self._parse_cached(text) if text else None
        wid = id(widget)
        self._observe(widget)
        if sheet is None:
            self.sources.pop(wid, None)
        else:
            self.sources[wid] = sheet
            self._merge_sheet_index(sheet)
        if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
            self._captured.add(wid)
        elif self.auto_discovery:
            self._prepared.add(wid)
        affected = self._invalidate_scope(widget)
        known = {id(target) for target in affected}
        # Registration can happen after the subtree's first Polish, including
        # before this matcher existed. Visit only the registered scope; no QSS
        # getters are needed to pick up its already-polished descendants.
        for target in widget.findChildren(QWidget):
            if id(target) not in known:
                self.invalidate_widget_id(id(target))
                if target.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
                    self._observe(target)
                    affected.append(target)
        return sheet, affected

    def discover(self, widget: QWidget) -> list[tuple[QWidget, list[QWidget]]]:
        """Return source owners and previously matched descendants affected by them."""
        if not self.auto_discovery:
            return []
        lineage: list[QWidget] = []
        ancestor: QWidget | None = widget
        while ancestor is not None and id(ancestor) not in self._captured:
            lineage.append(ancestor)
            ancestor = ancestor.parentWidget()
        owners: list[tuple[QWidget, list[QWidget]]] = []
        for owner in reversed(lineage):
            wid = id(owner)
            self._observe(owner)
            self._captured.add(wid)
            if wid in self._prepared:
                # The setter already cached the authored CSS. Reading Qt's cleaned
                # text here would lose transitions and create a second cache entry.
                self._prepared.discard(wid)
                if wid in self.sources:
                    owners.append((owner, []))
                continue
            text = owner.styleSheet()
            if not text:
                continue
            sheet = self._parse_cached(text)
            self.sources[wid] = sheet
            self._merge_sheet_index(sheet)
            owners.append((owner, self._invalidate_scope(owner)))
        return owners

    def _merge_sheet_index(self, sheet: LocalStyleSheet) -> None:
        source = sheet.matcher.index
        flags = self.index.flags
        # Scope depends on ancestry even when the sheet has no descendant selector.
        flags.has_descendant = True
        flags.has_effect |= source.flags.has_effect
        flags.has_border_radius |= source.flags.has_border_radius
        flags.has_hover |= source.flags.has_hover
        flags.has_active |= source.flags.has_active
        self.index.ancestor.ids.update(source.ancestor.ids)
        self.index.ancestor.tags.update(source.ancestor.tags)
        self.index.ancestor.classes.update(source.ancestor.classes)
        self.tracked_attrs.update(sheet.matcher.tracked_attrs)

    def build_quick_filters(self) -> None:
        super().build_quick_filters()
        self._scoped_cache.clear()
        self._master_sheet = (
            LocalStyleSheet(self.rules.qss, RuleMatcher([]), overlay_selectors(self.rules), False, is_master=True)
            if isinstance(self.rules, ParsedStyleRules)
            else None
        )
        for wid, sheet in self.sources.items():
            if sheet.is_master and self._master_sheet is not None:
                self.sources[wid] = self._master_sheet
        self._chains.clear()
        for sheet in self.sources.values():
            self._merge_sheet_index(sheet)

    def _source_chain(self, widget: QWidget) -> tuple[LocalStyleSheet, ...]:
        cached = self._chains.get(id(widget))
        if cached is not None:
            return cached
        lineage: list[QWidget] = []
        ancestor: QWidget | None = widget
        chain: tuple[LocalStyleSheet, ...] = ()
        while ancestor is not None:
            cached = self._chains.get(id(ancestor))
            if cached is not None:
                chain = cached
                break
            lineage.append(ancestor)
            ancestor = ancestor.parentWidget()
        for owner in reversed(lineage):
            self._observe(owner)
            sheet = self.sources.get(id(owner))
            if sheet is not None and not sheet.is_master:
                chain = (*chain, sheet)
            self._chains[id(owner)] = chain
        return chain

    def should_evaluate(self, widget: QWidget, ctx: WidgetState | None) -> bool:
        if self.auto_discovery and id(widget) not in self._captured:
            # Construction-time class/parent changes must not write over QSS that
            # application code will assign before this widget's first Polish.
            return False
        return super().should_evaluate(widget, ctx) or any(
            sheet.matcher.should_evaluate(widget, ctx) for sheet in self._source_chain(widget)
        )

    def matching_rules(self, widget: QWidget) -> list[StyleRule]:
        cached = self._scoped_cache.get(id(widget))
        if cached is not None:
            return cached
        matched = list(super().matching_rules(widget))
        for rank, sheet in enumerate(self._source_chain(widget), start=1):
            matched.extend(
                replace(rule, scope_rank=rank) for rule in sheet.matcher.matching_rules(widget) if not rule.subcontrol
            )
        self._scoped_cache.set(id(widget), widget, self.identity(widget), matched)
        return matched

    def invalidate_widget_id(self, wid: int) -> None:
        super().invalidate_widget_id(wid)
        self._scoped_cache.invalidate(wid)
        for sheet in self._chains.pop(wid, ()):
            sheet.matcher.invalidate_widget_id(wid)

    def forget_widget_id(self, wid: int) -> None:
        self.invalidate_widget_id(wid)
        self.sources.pop(wid, None)
        self._captured.discard(wid)
        self._prepared.discard(wid)
        self._refs.pop(wid, None)

    def clear_caches(self) -> None:
        super().clear_caches()
        self._scoped_cache.clear()
        self._chains.clear()
        sheets = {id(sheet): sheet for sheet in (*self.sheet_cache.values(), *self.sources.values())}
        for sheet in sheets.values():
            sheet.matcher.clear_caches()
