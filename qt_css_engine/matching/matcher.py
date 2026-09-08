import weakref
from typing import TYPE_CHECKING

from qt_css_engine.matching.cache import IdentityCache, WidgetCache
from qt_css_engine.matching.compiler import CompiledSegment, WidgetIdentity, attr_matches, compile_segment
from qt_css_engine.matching.index import StyleIndex
from qt_css_engine.qt_compat.QtCore import QObject
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.types import WidgetContext

if TYPE_CHECKING:
    from qt_css_engine.css.model import StyleRule


class RuleMatcher:
    """Consolidated matcher — single StyleIndex + two-level cache."""

    def __init__(self, rules: list[StyleRule]) -> None:
        self.rules = rules
        self.index = StyleIndex()
        self._segments: dict[str, CompiledSegment] = {}
        self.identity_cache = IdentityCache()
        self.widget_cache = WidgetCache()
        self.tracked_attrs: set[str] = set()
        self.build_quick_filters()

    # -- Index building -----------------------------------------------------------
    def build_quick_filters(self) -> None:
        self.index.clear()
        self._segments.clear()
        self.identity_cache.clear()
        self.widget_cache.clear()
        self.tracked_attrs = set()

        freq: dict[str, int] = {}
        last_segments: list[CompiledSegment | None] = []
        for rule in self.rules:
            if rule.segments:
                last_seg = self.segment(rule.segments[-1])
                last_segments.append(last_seg)
                for cls in last_seg.classes:
                    freq[cls] = freq.get(cls, 0) + 1
            else:
                last_segments.append(None)
            for seg in rule.segments:
                for cond in self.segment(seg).attrs:
                    self.tracked_attrs.add(cond.name)

        for index, rule in enumerate(self.rules):
            last_seg = last_segments[index]
            if last_seg is not None:
                self._index_rule(index, last_seg, freq)
            if len(rule.segments) > 1:
                self.index.flags.has_descendant = True
                for seg in rule.segments[:-1]:
                    compiled = self.segment(seg)
                    if compiled.obj_name is not None:
                        self.index.ancestor.ids.add(compiled.obj_name)
                    if compiled.tag is not None:
                        self.index.ancestor.tags.add(compiled.tag)
                    self.index.ancestor.classes.update(compiled.classes)
            if rule.subcontrol:
                continue
            has_effect = rule.has_effect_props
            has_cursor = rule.has_cursor_prop
            has_radius = rule.has_border_radius_props
            if not rule.transitions and not has_effect and not has_cursor and not has_radius:
                continue
            if has_effect or any(t.prop in ("opacity", "all") for t in rule.transitions):
                self.index.flags.has_effect = True
            if has_radius:
                self.index.flags.has_border_radius = True
            if last_seg is None:
                continue
            if last_seg.obj_name is not None:
                self.index.quick.ids.add(last_seg.obj_name)
            if last_seg.tag is not None:
                self.index.quick.tags.add(last_seg.tag)
            self.index.quick.classes.update(last_seg.classes)

    def _index_rule(self, index: int, last: CompiledSegment, freq: dict[str, int] | None = None) -> None:
        if last.obj_name is not None:
            self.index.buckets.by_id.setdefault(last.obj_name, []).append(index)
        elif last.tag is not None:
            self.index.buckets.by_tag.setdefault(last.tag, []).append(index)
        elif last.classes:
            if freq is None:
                key = min(last.classes)
            else:
                key = min(last.classes, key=lambda c: (freq.get(c, 0), c))
            self.index.buckets.by_class.setdefault(key, []).append(index)
        else:
            self.index.buckets.unconditional.append(index)

    def _candidate_indices(self, ident: WidgetIdentity) -> list[int]:
        buckets = self.index.buckets
        indices = list(buckets.unconditional)
        by_id = buckets.by_id.get(ident.obj_name)
        if by_id is not None:
            indices += by_id
        by_tag = buckets.by_tag.get(ident.tag)
        if by_tag is not None:
            indices += by_tag
        by_class = buckets.by_class
        if by_class:
            for cls in ident.classes:
                bucket = by_class.get(cls)
                if bucket is not None:
                    indices += bucket
        indices.sort()
        return indices

    def clear_caches(self) -> None:
        self.widget_cache.clear()
        self.identity_cache.clear()

    def invalidate_widget(self, widget: QWidget) -> None:
        self.invalidate_widget_id(id(widget))

    def invalidate_widget_id(self, wid: int) -> None:
        self.widget_cache.invalidate(wid)

    def is_ancestor_relevant(self, ident: WidgetIdentity) -> bool:
        """Whether a widget identity can anchor a descendant selector."""
        return self.index.ancestor.is_relevant(ident)

    def invalidate_subtree(self, widget: QWidget) -> None:
        wid = id(widget)
        if not self.index.flags.has_descendant:
            self.invalidate_widget_id(wid)
            return
        ident = self.identity(widget)
        previous = self.widget_cache.previous_ident(wid)
        relevant = self.is_ancestor_relevant(ident) or previous is None or self.is_ancestor_relevant(previous)
        if not relevant:
            self.invalidate_widget_id(wid)
        else:
            for cached_wid, widget_ref in list(self.widget_cache.refs.items()):
                cached_widget = widget_ref()
                if cached_widget is None:
                    self.invalidate_widget_id(cached_wid)
                    continue
                try:
                    if cached_widget is widget or self._is_descendant_of(cached_widget, widget):
                        self.invalidate_widget_id(cached_wid)
                except RuntimeError:
                    self.invalidate_widget_id(cached_wid)
        self.widget_cache.idents[wid] = ident
        if wid not in self.widget_cache.refs:
            self.widget_cache.refs[wid] = weakref.ref(widget, lambda _ref, _wid=wid: self.invalidate_widget_id(_wid))

    @staticmethod
    def _is_descendant_of(widget: QWidget, ancestor: QWidget) -> bool:
        parent: QObject | None = widget.parent()
        while parent is not None:
            if parent is ancestor:
                return True
            parent = parent.parent()
        return False

    def segment(self, segment: str) -> CompiledSegment:
        compiled = self._segments.get(segment)
        if compiled is None:
            compiled = compile_segment(segment)
            self._segments[segment] = compiled
        return compiled

    @staticmethod
    def identity(widget: QWidget) -> WidgetIdentity:
        raw: str = widget.property("class") or ""
        return WidgetIdentity(type(widget).__name__, widget.objectName(), frozenset(raw.split()))

    def should_evaluate(self, widget: QWidget, ctx: WidgetContext | None) -> bool:
        if bool(ctx and ctx.active_animations):
            return True
        if self.index.quick.ids and widget.objectName() in self.index.quick.ids:
            return True
        if self.index.quick.tags and type(widget).__name__ in self.index.quick.tags:
            return True
        if self.index.quick.classes:
            if not self.index.quick.classes.isdisjoint(self.widget_classes(widget)):
                return True
        return False

    @staticmethod
    def widget_classes(widget: QWidget) -> list[str]:
        raw: str = widget.property("class") or ""
        return raw.split()

    @staticmethod
    def identity_matches(ident: WidgetIdentity, seg: CompiledSegment) -> bool:
        """Structural match only (id/tag/classes) — ignores `[attr]` conditions."""
        if seg.obj_name is not None and ident.obj_name != seg.obj_name:
            return False
        if seg.tag is not None and ident.tag != seg.tag:
            return False
        return not seg.classes or seg.classes <= ident.classes

    @staticmethod
    def segment_attrs_match(widget: QWidget, seg: CompiledSegment) -> bool:
        """Check a widget's live dynamic properties against one segment's `[attr]` conditions."""
        if not seg.attrs:
            return True
        try:
            for cond in seg.attrs:
                if not attr_matches(widget.property(cond.name), cond):
                    return False
        except RuntimeError:
            return False
        return True

    def widget_matches_segment(self, widget: QWidget, segment: str) -> bool:
        seg = self.segment(segment)
        return self.identity_matches(self.identity(widget), seg) and self.segment_attrs_match(widget, seg)

    def ancestor_identities(self, widget: QWidget) -> list[WidgetIdentity]:
        idents: list[WidgetIdentity] = []
        ancestor: QObject | None = widget.parent()
        while ancestor is not None:
            if isinstance(ancestor, QWidget):
                idents.append(self.identity(ancestor))
            ancestor = ancestor.parent()
        return idents

    def match_ancestor_identities(self, idents: list[WidgetIdentity], segments: list[str]) -> bool:
        seg_idx = len(segments) - 2
        if seg_idx < 0:
            return True
        seg = self.segment(segments[seg_idx])
        for ident in idents:
            if self.identity_matches(ident, seg):
                seg_idx -= 1
                if seg_idx < 0:
                    return True
                seg = self.segment(segments[seg_idx])
        return False

    def match_ancestors(self, widget: QWidget, segments: list[str]) -> bool:
        """Structural + `[attr]` ancestor match (descendant combinator, in order)."""
        seg_idx = len(segments) - 2
        if seg_idx < 0:
            return True
        seg = self.segment(segments[seg_idx])
        ancestor: QObject | None = widget.parent()
        while ancestor is not None:
            if isinstance(ancestor, QWidget):
                try:
                    if self.identity_matches(self.identity(ancestor), seg) and self.segment_attrs_match(ancestor, seg):
                        seg_idx -= 1
                        if seg_idx < 0:
                            return True
                        seg = self.segment(segments[seg_idx])
                except RuntimeError:
                    return False
            ancestor = ancestor.parent()
        return False

    def rule_attrs_match(self, widget: QWidget, rule: StyleRule) -> bool:
        """True when every `[attr]` condition holds (widget + ancestors).

        matching_rules() already verified the structural (id/tag/class/ancestry)
        match; this re-walks the ancestor chain requiring both structural and
        attr conditions so `[attr]` on any segment gates the rule.
        """
        segments = rule.segments
        if not segments:
            return False
        if not self.segment_attrs_match(widget, self.segment(segments[-1])):
            return False
        if len(segments) == 1:
            return True
        return self.match_ancestors(widget, segments)

    def matches(self, widget: QWidget, rule: StyleRule) -> bool:
        """Full match: structural + `[attr]` (pseudos stay cascade-gated, as before)."""
        segments = rule.segments
        if not segments:
            return False
        if not self.widget_matches_segment(widget, segments[-1]):
            return False
        if len(segments) == 1:
            return True
        return self.match_ancestors(widget, segments)

    def matching_rules(self, widget: QWidget) -> list[StyleRule]:
        wid = id(widget)
        cached = self.widget_cache.get(wid)
        if cached is not None:
            return cached
        ident = self.identity(widget)
        candidates: list[StyleRule] | None = self.identity_cache.get(ident)
        if candidates is None:
            identity_matches = self.identity_matches
            segment = self.segment
            rules = self.rules
            candidates = []
            for i in self._candidate_indices(ident):
                rule = rules[i]
                if identity_matches(ident, segment(rule.segments[-1])):
                    candidates.append(rule)
            self.identity_cache.set(ident, candidates)
        if any(len(r.segments) > 1 for r in candidates):
            idents = self.ancestor_identities(widget)
            result = [
                r for r in candidates if len(r.segments) == 1 or self.match_ancestor_identities(idents, r.segments)
            ]
        else:
            result = candidates
        self.widget_cache.set(wid, widget, ident, result)
        return result

    def check_ancestors(self, widget: QWidget, rule: StyleRule) -> bool:
        return self.match_ancestor_identities(self.ancestor_identities(widget), rule.segments)
