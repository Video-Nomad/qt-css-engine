"""Cascade evaluation — collect base/target props, transitions, animated props."""

from qt_css_engine.constants import BORDER_RADIUS_PROPS, EFFECT_PROPS
from qt_css_engine.engine.evaluation import ResolvedRuleState
from qt_css_engine.geometry.clamp import clamp_border_radius, target_border_radius_box_size
from qt_css_engine.matching.matcher import RuleMatcher
from qt_css_engine.qt_compat.QtWidgets import QWidget
from qt_css_engine.state.widget_state import WidgetState
from qt_css_engine.utils.parsing import parse_css_numeric


class CascadeEvaluator:
    """Pure cascade logic — no animation, no style flushing."""

    def __init__(self, matcher: RuleMatcher, pseudo_priority: dict[str, int]) -> None:
        self._matcher = matcher
        self._priority = pseudo_priority

    def collect(self, widget: QWidget, ctx: WidgetState) -> ResolvedRuleState:
        state = ResolvedRuleState()
        # Per-prop winners: CSS2 (specificity, order) with the engine's pseudo
        # priority kept as a tiebreak inside equal specificity so :pressed still
        # beats :hover (and :clicked beats :pressed) regardless of source order.
        base_best: dict[str, tuple[tuple[int, int, int], int]] = {}
        target_best: dict[str, tuple[tuple[int, int, int], int, int]] = {}
        trans_best: dict[str, tuple[tuple[int, int, int], int, int]] = {}
        pseudos = ctx.active_pseudos
        for rule in self._matcher.matching_rules(widget):
            attrs_match = self._matcher.rule_attrs_match(widget, rule) if rule.has_attrs else True
            rule_in_target = attrs_match and (not rule.pseudo_set or rule.pseudo_set <= pseudos)
            priority = sum(self._priority.get(p, 0) for p in rule.pseudo_set) if rule_in_target else -1
            for trans in rule.transitions:
                state.animated_props.add(trans.prop)
                if rule_in_target:
                    key = (rule.specificity, priority, rule.order)
                    if key >= trans_best.get(trans.prop, ((-1, -1, -1), -1, -1)):
                        state.transitions[trans.prop] = trans
                        trans_best[trans.prop] = key
            # Attr-conditional rules ([active=true]) are target-only like pseudo
            # rules: the resting base must not shift with dynamic state, otherwise
            # the animation start point would already equal the target.
            if not rule.pseudo_set and not rule.has_attrs:
                for prop, val in rule.properties.items():
                    key = (rule.specificity, rule.order)
                    if key >= base_best.get(prop, ((-1, -1, -1), -1)):
                        state.base_props[prop] = val
                        base_best[prop] = key
            if rule_in_target:
                for prop, val in rule.properties.items():
                    key = (rule.specificity, priority, rule.order)
                    if key >= target_best.get(prop, ((-1, -1, -1), -1, -1)):
                        state.target_props[prop] = val
                        target_best[prop] = key
        if "all" in state.animated_props:
            self.expand_all(ctx, state)
        for prop in EFFECT_PROPS:
            if prop in state.base_props or prop in state.target_props:
                state.animated_props.add(prop)
        self.collect_border_radius(widget, ctx, state)
        return state

    def expand_all(self, ctx: WidgetState, state: ResolvedRuleState) -> None:
        all_spec = state.transitions.pop("all", None)
        state.animated_props.discard("all")
        for prop in set(state.base_props) | set(state.target_props):
            if self._is_animatable(prop):
                state.animated_props.add(prop)
                if prop not in state.transitions and all_spec is not None:
                    state.transitions[prop] = all_spec
        if all_spec is not None:
            engine_managed: set[str] = set(ctx.css_anim_props) | set(ctx.active_animations)
            for prop in engine_managed:
                if prop not in state.animated_props and self._is_animatable(prop) and prop not in EFFECT_PROPS:
                    state.animated_props.add(prop)
                    state.transitions[prop] = all_spec

    def collect_border_radius(self, widget: QWidget, ctx: WidgetState, state: ResolvedRuleState) -> None:
        box_size: tuple[float, float] | None = None
        box_resolved = False
        for prop in BORDER_RADIUS_PROPS:
            if prop not in state.target_props:
                continue
            if prop in ctx.css_anim_props:
                state.animated_props.add(prop)
                continue
            parsed = parse_css_numeric(state.target_props.get(prop))
            if parsed is None:
                continue
            value, unit = parsed
            if unit != "px":
                continue
            if not box_resolved:
                box_size = target_border_radius_box_size(widget, state.target_props)
                box_resolved = True
            if clamp_border_radius(widget, prop, max(0.0, value), unit, state.target_props, box_size) != value:
                state.animated_props.add(prop)

    def needs_qt_border_radius_clamp(self, widget: QWidget, target_props: dict[str, str], prop: str) -> bool:
        if prop not in BORDER_RADIUS_PROPS:
            return False
        parsed = parse_css_numeric(target_props.get(prop))
        if parsed is None:
            return False
        value, unit = parsed
        if unit != "px":
            return False
        box_size = target_border_radius_box_size(widget, target_props)
        return clamp_border_radius(widget, prop, max(0.0, value), unit, target_props, box_size) != value

    @staticmethod
    def _is_animatable(prop: str) -> bool:
        from qt_css_engine.constants import EFFECT_PROPS, SUPPORTED_NUMERIC_PROPS

        if prop == "color" or prop.endswith("-color"):
            return True
        return prop in EFFECT_PROPS or prop in SUPPORTED_NUMERIC_PROPS
