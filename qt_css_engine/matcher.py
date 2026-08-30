import weakref
from itertools import islice
from typing import TYPE_CHECKING, NamedTuple

from qt_css_engine.qt_compat.QtCore import QObject
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.types import WidgetContext

if TYPE_CHECKING:
    from qt_css_engine.css_parser import StyleRule

# Upper bound on distinct widget identities kept in the candidate cache.
CANDIDATE_CACHE_MAX = 2048


class CompiledSegment(NamedTuple):
    """A selector segment pre-split into the three things a widget is tested against."""

    obj_name: str | None  # required objectName, from a '#id' head
    tag: str | None  # required type name, from a 'QLabel' head
    classes: frozenset[str]  # required CSS class tokens


class WidgetIdentity(NamedTuple):
    """Everything about a widget that selector matching depends on, read from Qt once."""

    tag: str
    obj_name: str
    classes: frozenset[str]


def compile_segment(segment: str) -> CompiledSegment:
    """Split one selector segment into its objectName / type / class requirements."""
    parts = segment.split(".")
    head = parts[0]
    classes = frozenset(parts[1:])
    if head.startswith("#"):
        return CompiledSegment(head[1:], None, classes)
    return CompiledSegment(None, head or None, classes)


class RuleMatcher:
    """Handles CSS rule and selector matching for widgets, including quick filters and rule caches."""

    def __init__(self, rules: list[StyleRule]) -> None:
        self.rules = rules
        # Quick filters: sets of segments[-1] parts that have transitions or effect props
        self.animated_tags: set[str] = set()
        self.animated_classes: set[str] = set()
        self.animated_ids: set[str] = set()
        # Flags populated at quick filter build time
        self.has_effect_rules: bool = False
        self.has_border_radius_rules: bool = False
        self.has_cursor_rules: bool = False
        # True when any rule uses a descendant combinator ('.parent .child').  When False a
        # widget's match set cannot depend on its ancestors, so reparenting or a class change
        # elsewhere in the tree can never invalidate another widget's cached result.
        self.has_descendant_selectors: bool = False

        # Selector segment string -> its compiled form.  Segments repeat heavily across rules
        # ('.widget' appears in most selectors), so this stays small and hits constantly.
        self._segments: dict[str, CompiledSegment] = {}

        # Everything that can appear in a *non-final* selector segment, i.e. in an ancestor
        # position.  A widget matching none of these can never change another widget's match
        # set, so a class change on it stays local instead of invalidating every widget.
        self.ancestor_tags: set[str] = set()
        self.ancestor_classes: set[str] = set()
        self.ancestor_ids: set[str] = set()

        # Inverted index over each rule's final segment, so resolving a widget looks at the
        # rules that could plausibly apply to it rather than scanning the whole sheet.  Every
        # rule lives in exactly one bucket, chosen most-selective-first; values are indices
        # into self.rules so the cascade order can be restored by sorting.
        self._by_id: dict[str, list[int]] = {}
        self._by_tag: dict[str, list[int]] = {}
        self._by_class: dict[str, list[int]] = {}
        self._unconditional: list[int] = []

        # Two-level caches
        self.type_class_rule_cache: dict[WidgetIdentity, list[StyleRule]] = {}
        self.rule_cache: dict[int, list[StyleRule]] = {}
        # rule_cache is keyed by id(widget), which CPython reuses once a widget is freed.  A
        # widget landing on a recycled address would otherwise inherit the dead widget's rules.
        # These weakrefs evict the entry exactly when the address becomes reusable, which also
        # stops the cache growing without bound in apps that churn widgets.
        self._cache_refs: dict[int, weakref.ref[QWidget]] = {}
        # Identity a cache entry was built with, so a later class change can tell whether the
        # tokens that actually changed are ones any rule uses in an ancestor position.
        self._cached_idents: dict[int, WidgetIdentity] = {}

        self.build_quick_filters()

    def build_quick_filters(self) -> None:
        """Rebuild animated tag/class/id sets from current rules for fast pre-filtering."""
        self.animated_tags.clear()
        self.animated_classes.clear()
        self.animated_ids.clear()
        self._segments.clear()
        self.ancestor_tags.clear()
        self.ancestor_classes.clear()
        self.ancestor_ids.clear()
        self._by_id.clear()
        self._by_tag.clear()
        self._by_class.clear()
        self._unconditional.clear()
        self.has_effect_rules = False
        self.has_border_radius_rules = False
        self.has_cursor_rules = False
        self.has_descendant_selectors = False

        for index, rule in enumerate(self.rules):
            if rule.segments:
                self._index_rule(index, self.segment(rule.segments[-1]))
            if len(rule.segments) > 1:
                self.has_descendant_selectors = True
                for seg in rule.segments[:-1]:
                    compiled = self.segment(seg)
                    if compiled.obj_name is not None:
                        self.ancestor_ids.add(compiled.obj_name)
                    if compiled.tag is not None:
                        self.ancestor_tags.add(compiled.tag)
                    self.ancestor_classes.update(compiled.classes)
            if rule.subcontrol:
                continue
            has_effect_props = rule.has_effect_props
            has_cursor_props = rule.has_cursor_prop
            has_border_radius_props = rule.has_border_radius_props
            if not rule.transitions and not has_effect_props and not has_cursor_props and not has_border_radius_props:
                continue
            if has_effect_props or any(t.prop in ("opacity", "all") for t in rule.transitions):
                self.has_effect_rules = True
            if has_border_radius_props:
                self.has_border_radius_rules = True
            if has_cursor_props:
                self.has_cursor_rules = True
            if not rule.segments:
                continue
            last_segment = rule.segments[-1]
            if last_segment.startswith("#"):
                self.animated_ids.add(last_segment.split(".")[0][1:])
            elif last_segment.startswith("."):
                for cls in last_segment.split(".")[1:]:
                    self.animated_classes.add(cls)
            else:
                parts = last_segment.split(".")
                if parts[0]:
                    self.animated_tags.add(parts[0])
                for cls in parts[1:]:
                    self.animated_classes.add(cls)

    def _index_rule(self, index: int, last: CompiledSegment) -> None:
        """File a rule under the most selective requirement of its final segment."""
        if last.obj_name is not None:
            self._by_id.setdefault(last.obj_name, []).append(index)
        elif last.tag is not None:
            self._by_tag.setdefault(last.tag, []).append(index)
        elif last.classes:
            # Any one required class is enough to disqualify the rule; identity_matches()
            # still verifies the rest, so which token is used as the key does not matter.
            self._by_class.setdefault(next(iter(last.classes)), []).append(index)
        else:
            self._unconditional.append(index)

    def _candidate_indices(self, ident: WidgetIdentity) -> list[int]:
        """Rule indices whose final segment could match this identity, in cascade order."""
        indices = list(self._unconditional)
        by_id = self._by_id.get(ident.obj_name)
        if by_id is not None:
            indices += by_id
        by_tag = self._by_tag.get(ident.tag)
        if by_tag is not None:
            indices += by_tag
        by_class = self._by_class
        if by_class:
            for cls in ident.classes:
                bucket = by_class.get(cls)
                if bucket is not None:
                    indices += bucket
        indices.sort()
        return indices

    def clear_caches(self) -> None:
        """Clear rule match caches."""
        self.rule_cache.clear()
        self._cache_refs.clear()
        self._cached_idents.clear()
        self.type_class_rule_cache.clear()

    def invalidate_widget(self, widget: QWidget) -> None:
        """Drop the cached match list for a single widget."""
        self.invalidate_widget_id(id(widget))

    def invalidate_widget_id(self, wid: int) -> None:
        """Drop the cached match list for a single widget id."""
        self.rule_cache.pop(wid, None)
        self._cache_refs.pop(wid, None)
        self._cached_idents.pop(wid, None)

    def _is_ancestor_relevant(self, ident: WidgetIdentity) -> bool:
        """True if a widget with this identity can satisfy some rule's ancestor segment."""
        return (
            ident.obj_name in self.ancestor_ids
            or ident.tag in self.ancestor_tags
            or not self.ancestor_classes.isdisjoint(ident.classes)
        )

    def invalidate_subtree(self, widget: QWidget) -> None:
        """
        Drop cached match lists invalidated by a class change at *widget*.

        The candidate cache (``type_class_rule_cache``) is keyed by widget identity and is a
        pure function of that identity plus the rule set, so it is never affected here — a
        widget whose class changed simply looks up a different key.

        Only ``rule_cache`` is at risk, and only for descendants of *widget*: a widget's own
        entry plus every entry that could have matched *widget* in an ancestor position. When
        *widget* can appear in no rule's ancestor segment — the usual case for a state class
        toggled on a leaf — the change is purely local and nothing else is touched.
        """
        wid = id(widget)
        if not self.has_descendant_selectors:
            self.invalidate_widget_id(wid)
            return
        # Compare both the identity the cache was built with and the current one: a class that
        # was *removed* matters just as much as one that was added. An unseen widget has no
        # known previous identity, so treat it as relevant.
        ident = self.identity(widget)
        previous = self._cached_idents.get(wid)
        relevant = self._is_ancestor_relevant(ident) or previous is None or self._is_ancestor_relevant(previous)
        if relevant:
            self.rule_cache.clear()
            self._cache_refs.clear()
            self._cached_idents.clear()
        else:
            self.invalidate_widget_id(wid)
        # Remember what the widget looks like now so the next change on it can be compared
        # against a known previous state instead of falling back to the conservative sweep.
        self._cached_idents[wid] = ident
        self._cache_refs.setdefault(wid, weakref.ref(widget, lambda _ref, _wid=wid: self.invalidate_widget_id(_wid)))

    def segment(self, segment: str) -> CompiledSegment:
        """Return the compiled form of a selector segment, compiling on first use."""
        compiled = self._segments.get(segment)
        if compiled is None:
            compiled = compile_segment(segment)
            self._segments[segment] = compiled
        return compiled

    @staticmethod
    def identity(widget: QWidget) -> WidgetIdentity:
        """Read the three widget attributes selector matching depends on, in one pass."""
        raw: str = widget.property("class") or ""
        return WidgetIdentity(type(widget).__name__, widget.objectName(), frozenset(raw.split()))

    def should_evaluate(self, widget: QWidget, ctx: WidgetContext | None) -> bool:
        """Return True if the widget could be affected by any animated CSS rule."""
        if bool(ctx and ctx.active_animations):
            return True
        if self.animated_ids and widget.objectName() in self.animated_ids:
            return True
        if self.animated_tags and type(widget).__name__ in self.animated_tags:
            return True
        if self.animated_classes:
            if not self.animated_classes.isdisjoint(self.widget_classes(widget)):
                return True
        return False

    @staticmethod
    def widget_classes(widget: QWidget) -> list[str]:
        """Return the CSS class tokens from the widget's 'class' property."""
        raw: str = widget.property("class") or ""
        return raw.split()

    @staticmethod
    def identity_matches(ident: WidgetIdentity, seg: CompiledSegment) -> bool:
        """Return True if a widget identity satisfies a compiled selector segment."""
        if seg.obj_name is not None and ident.obj_name != seg.obj_name:
            return False
        if seg.tag is not None and ident.tag != seg.tag:
            return False
        return not seg.classes or seg.classes <= ident.classes

    def widget_matches_segment(self, widget: QWidget, segment: str) -> bool:
        """Return True if widget matches a single selector segment (id, class, or tag)."""
        return self.identity_matches(self.identity(widget), self.segment(segment))

    def ancestor_identities(self, widget: QWidget) -> list[WidgetIdentity]:
        """Widget identities of every QWidget ancestor, nearest first."""
        idents: list[WidgetIdentity] = []
        ancestor: QObject | None = widget.parent()
        while ancestor is not None:
            if isinstance(ancestor, QWidget):
                idents.append(self.identity(ancestor))
            ancestor = ancestor.parent()
        return idents

    def match_ancestor_identities(self, idents: list[WidgetIdentity], segments: list[str]) -> bool:
        """Greedily match a rule's leading segments against a precomputed ancestor chain."""
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

    def matches(self, widget: QWidget, rule: StyleRule) -> bool:
        """Return True if widget matches a full descendant-combinator selector."""
        segments = rule.segments
        if not segments:
            return False
        if not self.identity_matches(self.identity(widget), self.segment(segments[-1])):
            return False
        if len(segments) == 1:
            return True
        return self.match_ancestor_identities(self.ancestor_identities(widget), segments)

    def matching_rules(self, widget: QWidget) -> list[StyleRule]:
        """
        Return rules matching widget, using per-widget cached results when possible.
        """
        wid = id(widget)
        cached = self.rule_cache.get(wid)
        if cached is not None:
            return cached
        # Get or build the candidate list (last-segment match only) for this widget identity.
        ident = self.identity(widget)
        candidates: list[StyleRule] | None = self.type_class_rule_cache.get(ident)
        if candidates is None:
            identity_matches = self.identity_matches
            segment = self.segment
            rules = self.rules
            candidates = []
            for i in self._candidate_indices(ident):
                rule = rules[i]
                if identity_matches(ident, segment(rule.segments[-1])):
                    candidates.append(rule)
            # Keyed by identity, so an app that mints a distinct objectName per widget would
            # otherwise grow this forever. Evict oldest-first; it is a pure cache and a miss
            # only costs a rebuild.
            if len(self.type_class_rule_cache) >= CANDIDATE_CACHE_MAX:
                for stale in list(islice(self.type_class_rule_cache, CANDIDATE_CACHE_MAX // 4)):
                    del self.type_class_rule_cache[stale]
            self.type_class_rule_cache[ident] = candidates
        # Filter candidates by ancestor chain.  The chain is read from Qt once and shared
        # across every candidate rule instead of being re-walked per rule.
        if any(len(r.segments) > 1 for r in candidates):
            idents = self.ancestor_identities(widget)
            result = [
                r for r in candidates if len(r.segments) == 1 or self.match_ancestor_identities(idents, r.segments)
            ]
        else:
            result = candidates
        self.rule_cache[wid] = result
        self._cached_idents[wid] = ident
        self._cache_refs[wid] = weakref.ref(widget, lambda _ref, _wid=wid: self.invalidate_widget_id(_wid))
        return result

    def check_ancestors(self, widget: QWidget, rule: StyleRule) -> bool:
        """Check the ancestor chain for a rule whose last segment already matched."""
        return self.match_ancestor_identities(self.ancestor_identities(widget), rule.segments)
