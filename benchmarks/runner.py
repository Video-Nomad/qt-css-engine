"""Timing helpers for benchmarks."""

import gc
import statistics
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
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
    samples_ms: list[float]
    warmup: int
    extra: dict[str, object] | None = None


@dataclass
class Workload:
    run: Callable[[], None]
    validate: Callable[[], None]
    extra: dict[str, object]
    iterations: int = 1


def measure(
    name: str,
    factory: Callable[[], AbstractContextManager[Workload]],
    *,
    warmup: int,
    runs: int,
    writes: bool = False,
) -> BenchResult:
    """Fresh fixtures per sample; setup, assertions, GC and teardown are untimed.

    GC is disabled only during the operation and its previous state is restored
    even on failure. Write instrumentation runs in a separate diagnostic sample.
    """
    if warmup < 0 or runs < 1:
        raise ValueError("warmup must be >= 0 and runs must be >= 1")
    samples: list[float] = []
    extra: dict[str, object] = {}
    for index in range(warmup + runs):
        with factory() as workload:
            gc.collect()
            was_enabled = gc.isenabled()
            gc.disable()
            try:
                start = time.perf_counter_ns()
                workload.run()
                elapsed = (time.perf_counter_ns() - start) / 1_000_000 / workload.iterations
            finally:
                if was_enabled:
                    gc.enable()
            workload.validate()
            if index >= warmup:
                samples.append(elapsed)
            extra = {**workload.extra, "iterations_per_sample": workload.iterations}
    if writes:
        from benchmarks.common import count_writes

        with factory() as workload:
            with count_writes() as counter:
                workload.run()
            workload.validate()
            extra["diagnostic_writes"] = counter["count"]
    return summarize(name, samples, warmup=warmup, extra=extra)


def summarize(
    name: str, times_ms: list[float], *, warmup: int = 0, extra: dict[str, object] | None = None
) -> BenchResult:
    return BenchResult(
        name=name,
        mean_ms=statistics.mean(times_ms),
        median_ms=statistics.median(times_ms),
        min_ms=min(times_ms),
        max_ms=max(times_ms),
        stdev_ms=statistics.pstdev(times_ms) if len(times_ms) > 1 else 0.0,
        runs=len(times_ms),
        samples_ms=times_ms,
        warmup=warmup,
        extra=extra,
    )


def _extra_str(extra: dict[str, object] | None) -> str:
    if not extra:
        return ""
    # Prefer compact representation: writes / widgets / rules
    parts: list[str] = []
    if "diagnostic_writes" in extra:
        parts.append(f"{extra['diagnostic_writes']} writes (untimed)")
    if "widgets" in extra and "rules" in extra:
        parts.append(f"{extra['widgets']}w {extra['rules']}r")
    elif "widgets" in extra:
        parts.append(f"{extra['widgets']}w")
    if "resized" in extra:
        parts.append(f"{extra['resized']} resized")
    if "frames" in extra:
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
                f"{r.mean_ms:.3f}",
                f"{r.median_ms:.3f}",
                f"{r.min_ms:.3f}",
                f"{r.max_ms:.3f}",
                f"{r.stdev_ms:.3f}",
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
