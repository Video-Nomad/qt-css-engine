from typing import TYPE_CHECKING

from qt_css_engine.constants import BORDER_RADIUS_PROPS, EFFECT_PROPS
from qt_css_engine.qt_compat.QtCore import QObject
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.types import WidgetContext

if TYPE_CHECKING:
    from qt_css_engine.css_parser import StyleRule


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

        # Two-level caches
        self.type_class_rule_cache: dict[tuple[str, str], list[StyleRule]] = {}
        self.rule_cache: dict[int, list[StyleRule]] = {}

        self.build_quick_filters()

    def build_quick_filters(self) -> None:
        """Rebuild animated tag/class/id sets from current rules for fast pre-filtering."""
        self.animated_tags.clear()
        self.animated_classes.clear()
        self.animated_ids.clear()
        self.has_effect_rules = False
        self.has_border_radius_rules = False
        self.has_cursor_rules = False

        for rule in self.rules:
            if rule.subcontrol:
                continue
            has_effect_props = any(p in EFFECT_PROPS for p in rule.properties)
            has_cursor_props = "cursor" in rule.properties
            has_border_radius_props = any(p in BORDER_RADIUS_PROPS for p in rule.properties)
            if not rule.transitions and not has_effect_props and not has_cursor_props and not has_border_radius_props:
                continue
            if has_effect_props or any(t.prop in ("opacity", "all") for t in rule.transitions):
                self.has_effect_rules = True
            if has_border_radius_props:
                self.has_border_radius_rules = True
            if has_cursor_props:
                self.has_cursor_rules = True
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

    def clear_caches(self) -> None:
        """Clear rule match caches."""
        self.rule_cache.clear()
        self.type_class_rule_cache.clear()

    def should_evaluate(self, widget: QWidget, ctx: WidgetContext | None) -> bool:
        """Return True if the widget could be affected by any animated CSS rule."""
        if bool(ctx and ctx.active_animations):
            return True
        if self.animated_ids and widget.objectName() in self.animated_ids:
            return True
        if self.animated_tags and type(widget).__name__ in self.animated_tags:
            return True
        if self.animated_classes:
            if any(cls in self.animated_classes for cls in self.widget_classes(widget)):
                return True
        return False

    @staticmethod
    def widget_classes(widget: QWidget) -> list[str]:
        """Return the CSS class tokens from the widget's 'class' property."""
        raw: str = widget.property("class") or ""
        return raw.split()

    def widget_matches_segment(self, widget: QWidget, segment: str) -> bool:
        """Return True if widget matches a single selector segment (id, class, or tag)."""
        if segment.startswith("#"):
            parts = segment.split(".")
            if widget.objectName() != parts[0][1:]:
                return False
            if len(parts) > 1:
                return all(cls in self.widget_classes(widget) for cls in parts[1:])
            return True
        if segment.startswith("."):
            parts = segment.split(".")
            return all(cls in self.widget_classes(widget) for cls in parts[1:])
        parts = segment.split(".")
        tag_name = parts[0]
        if tag_name and type(widget).__name__ != tag_name:
            return False
        if len(parts) > 1:
            return all(cls in self.widget_classes(widget) for cls in parts[1:])
        return True

    def matches(self, widget: QWidget, rule: StyleRule) -> bool:
        """Return True if widget matches a full descendant-combinator selector."""
        segments = rule.segments
        if not segments:
            return False
        if not self.widget_matches_segment(widget, segments[-1]):
            return False
        if len(segments) == 1:
            return True
        seg_idx = len(segments) - 2
        ancestor: QObject | None = widget.parent()
        while ancestor and seg_idx >= 0:
            if isinstance(ancestor, QWidget) and self.widget_matches_segment(ancestor, segments[seg_idx]):
                seg_idx -= 1
            ancestor = ancestor.parent()
        return seg_idx < 0

    def matching_rules(self, widget: QWidget) -> list[StyleRule]:
        """
        Return rules matching widget, using per-widget cached results when possible.
        """
        wid = id(widget)
        cached = self.rule_cache.get(wid)
        if cached is not None:
            return cached
        # Get or build the candidate list (last-segment match only) for this type+class.
        type_name = type(widget).__name__
        class_str = widget.property("class") or ""
        key = (type_name, class_str)
        candidates = self.type_class_rule_cache.get(key)
        if candidates is None:
            candidates = [r for r in self.rules if r.segments and self.widget_matches_segment(widget, r.segments[-1])]
            self.type_class_rule_cache[key] = candidates
        # Filter candidates by ancestor chain
        result = [r for r in candidates if len(r.segments) == 1 or self.check_ancestors(widget, r)]
        self.rule_cache[wid] = result
        return result

    def check_ancestors(self, widget: QWidget, rule: StyleRule) -> bool:
        """Check the ancestor chain for a rule whose last segment already matched."""
        seg_idx = len(rule.segments) - 2
        ancestor: QObject | None = widget.parent()
        while ancestor and seg_idx >= 0:
            if isinstance(ancestor, QWidget) and self.widget_matches_segment(ancestor, rule.segments[seg_idx]):
                seg_idx -= 1
            ancestor = ancestor.parent()
        return seg_idx < 0
