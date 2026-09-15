"""Shared steps() reversal math (curve check lives on StepsReversalMixin)."""


def steps_seek_ms(duration: int, current_ms: int) -> tuple[int, float]:
    dur = max(1, duration)
    raw_p = min(current_ms, dur) / dur
    seek = int((1.0 - raw_p) * dur)
    return seek, raw_p
