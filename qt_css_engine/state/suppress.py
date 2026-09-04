"""Internal write suppression — context manager for polish/measure guards."""

from collections.abc import Generator
from contextlib import contextmanager

from qt_css_engine.types import InternalWriteReason, WidgetContext


@contextmanager
def suppress(ctx: WidgetContext, reason: InternalWriteReason) -> Generator[None]:
    ctx.internal_write_depth += 1
    prev_reason = ctx.internal_write_reason
    ctx.internal_write_reason = reason
    try:
        yield
    finally:
        ctx.internal_write_depth -= 1
        if ctx.internal_write_depth == 0:
            ctx.internal_write_reason = None
        else:
            ctx.internal_write_reason = prev_reason


def is_suppressed(ctx: WidgetContext | None) -> bool:
    return bool(ctx and ctx.internal_write_depth > 0)
