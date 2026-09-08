"""Timing helpers for benchmarks."""

import gc
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BenchResult:
    name: str
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    runs: int
    extra: dict[str, object] | None = None


def time_call(
    fn: Callable[[], None],
    *,
    warmup: int = 2,
    runs: int = 7,
    gc_collect: bool = True,
) -> list[float]:
    """Time *fn* over *runs* iterations (ms), with *warmup* untimed runs."""
    for _ in range(warmup):
        fn()
        if gc_collect:
            gc.collect()

    times: list[float] = []
    for _ in range(runs):
        if gc_collect:
            gc.collect()
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)
    return times


def summarize(name: str, times_ms: list[float], extra: dict[str, object] | None = None) -> BenchResult:
    return BenchResult(
        name=name,
        mean_ms=statistics.mean(times_ms),
        median_ms=statistics.median(times_ms),
        min_ms=min(times_ms),
        max_ms=max(times_ms),
        stdev_ms=statistics.pstdev(times_ms) if len(times_ms) > 1 else 0.0,
        runs=len(times_ms),
        extra=extra,
    )


def _extra_str(extra: dict[str, object] | None) -> str:
    if not extra:
        return ""
    # Prefer compact representation: writes / widgets / rules
    parts: list[str] = []
    if "writes_last_run" in extra:
        parts.append(f"{extra['writes_last_run']} writes")
    if "widgets" in extra and "rules" in extra:
        parts.append(f"{extra['widgets']}w {extra['rules']}r")
    elif "widgets" in extra:
        parts.append(f"{extra['widgets']}w")
    if "resized" in extra:
        parts.append(f"{extra['resized']} resized")
    if "frames" in extra and "writes_last_run" not in extra:
        parts.append(f"{extra['frames']}f")
    if not parts:
        # Fallback: short dict without braces
        return ", ".join(f"{k}={v}" for k, v in extra.items())
    return ", ".join(parts)


def format_table(results: list[BenchResult]) -> str:
    # Plain-text aligned table, not markdown. Extra is a column, not a separate bullet list.
    headers = ["Scenario", "Mean", "Median", "Min", "Max", "Stdev", "Runs", "Extra"]
    rows: list[list[str]] = []
    for r in results:
        rows.append(
            [
                r.name,
                f"{r.mean_ms:.2f}",
                f"{r.median_ms:.2f}",
                f"{r.min_ms:.2f}",
                f"{r.max_ms:.2f}",
                f"{r.stdev_ms:.2f}",
                str(r.runs),
                _extra_str(r.extra),
            ]
        )

    # Compute column widths
    col_widths: list[int] = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build lines
    def _fmt_row(cells: list[str], aligns: list[str]) -> str:
        out: list[str] = []
        for i, cell in enumerate(cells):
            w = col_widths[i]
            if aligns[i] == "l":
                out.append(cell.ljust(w))
            else:
                out.append(cell.rjust(w))
        return "  ".join(out)

    header_aligns = ["l", "r", "r", "r", "r", "r", "r", "l"]
    lines: list[str] = []
    lines.append(_fmt_row(headers, header_aligns))
    # Separator
    sep = "  ".join("-" * w for w in col_widths)
    lines.append(sep)
    for row in rows:
        lines.append(_fmt_row(row, header_aligns))
    return "\n".join(lines)
