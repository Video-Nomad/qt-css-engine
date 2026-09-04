"""Animation registry — per-widget active animators and orphan detection."""

from qt_css_engine.types import WidgetContext


class AnimationRegistry:
    @staticmethod
    def is_orphan(ctx: WidgetContext, prop: str, animated_props: set[str]) -> bool:
        return prop not in animated_props
