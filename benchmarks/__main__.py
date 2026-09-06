# pyright: reportPrivateUsage=false
"""Single entry point for the benchmark suite.

Usage:
    uv run python -m benchmarks                 # run all benchmarks
    uv run python -m benchmarks --list          # list available benchmarks
    uv run python -m benchmarks --filter cold   # run matching benchmarks
    uv run python -m benchmarks --json          # JSON output
    uv run python -m benchmarks --repeat 3      # fewer runs (faster)
    QT_API=pyside6 PYTHONHASHSEED=0 uv run python -m benchmarks  # fixed-hash, PySide6 offscreen

The suite reproduces the baseline workload:
    458 rules, 321 widgets, 40 widgets animating five properties over 15 frames.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

# Ensure offscreen before Qt loads
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from benchmarks import (
    bench_attr_change,
    bench_attr_matching,
    bench_class_anim_frames,
    bench_class_change,
    bench_cold_matching,
    bench_hover_frames,
    bench_initial_eval,
    bench_resize_storm,
    bench_warm_matching,
)
from benchmarks.runner import BenchResult, format_table


@dataclass(frozen=True)
class BenchEntry:
    key: str
    name: str
    benchmark: Callable[..., BenchResult]


# Order for baseline table
ALL_BENCHES: list[BenchEntry] = [
    BenchEntry("cold", bench_cold_matching.NAME, bench_cold_matching.benchmark),
    BenchEntry("warm", bench_warm_matching.NAME, bench_warm_matching.benchmark),
    BenchEntry("initial", bench_initial_eval.NAME, bench_initial_eval.benchmark),
    BenchEntry("attr_cold", bench_attr_matching.NAME_COLD, bench_attr_matching.benchmark_cold),
    BenchEntry("attr_warm", bench_attr_matching.NAME_WARM, bench_attr_matching.benchmark_warm),
    BenchEntry("attr_initial", bench_attr_matching.NAME_INITIAL, bench_attr_matching.benchmark_initial),
    BenchEntry("class_change", bench_class_change.NAME, bench_class_change.benchmark),
    BenchEntry("attr", bench_attr_change.NAME, bench_attr_change.benchmark),
    BenchEntry("class_anim", bench_class_anim_frames.NAME, bench_class_anim_frames.benchmark),
    BenchEntry("hover_anim", bench_hover_frames.NAME, bench_hover_frames.benchmark),
    BenchEntry("resize", bench_resize_storm.NAME, bench_resize_storm.benchmark),
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="qt_css_engine benchmark suite (baseline)")
    p.add_argument("--list", action="store_true", help="list benchmarks and exit")
    p.add_argument("--filter", type=str, default=None, help="substring filter (e.g. cold, class, hover)")
    p.add_argument("--json", action="store_true", help="output JSON instead of table")
    p.add_argument("--repeat", type=int, default=None, help="override default runs per benchmark")
    p.add_argument("--warmup", type=int, default=None, help="override warmup runs")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress per-benchmark progress, only print final table")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list:
        print("Available benchmarks:")
        for entry in ALL_BENCHES:
            print(f"  {entry.key:12} — {entry.name}")
        return

    # Select benchmarks
    selected: list[BenchEntry] = []
    for entry in ALL_BENCHES:
        if (
            args.filter
            and args.filter.lower() not in entry.key.lower()
            and args.filter.lower() not in entry.name.lower()
        ):
            continue
        selected.append(entry)

    if not selected:
        print(f"No benchmarks match filter {args.filter!r}", file=sys.stderr)
        sys.exit(1)

    quiet: bool = bool(args.quiet)

    if not quiet:
        print(
            f"Running {len(selected)} benchmark(s) — QT_API={os.environ.get('QT_API', '(auto)')}, "
            f"PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(random)')}, "
            f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM')}"
        )
        try:
            from qt_css_engine.qt_compat._api import USE_PYSIDE6

            binding = "PySide6" if USE_PYSIDE6 else "PyQt6"
        except Exception:
            binding = "unknown"
        print(f"Binding: {binding}\n")

    results: list[BenchResult] = []
    for entry in selected:
        if not quiet:
            print(f"→ {entry.name} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            if args.repeat is not None and args.warmup is not None:
                res = entry.benchmark(warmup=args.warmup, runs=args.repeat)
            elif args.repeat is not None:
                res = entry.benchmark(runs=args.repeat)
            elif args.warmup is not None:
                res = entry.benchmark(warmup=args.warmup)
            else:
                res = entry.benchmark()
        except Exception as e:
            if not quiet:
                print(f"FAILED: {e}")
            import traceback

            traceback.print_exc()
            continue
        t1 = time.perf_counter()
        if not quiet:
            print(f"{res.median_ms:.2f} ms median (mean {res.mean_ms:.2f} ms, {res.runs} runs, wall {t1 - t0:.1f}s)")
        results.append(res)

    if not results:
        print("No results.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        payload = [
            {
                "name": r.name,
                "mean_ms": r.mean_ms,
                "median_ms": r.median_ms,
                "min_ms": r.min_ms,
                "max_ms": r.max_ms,
                "stdev_ms": r.stdev_ms,
                "runs": r.runs,
                "extra": r.extra,
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        if not quiet:
            print()
        print(format_table(results))
        if not quiet:
            print("\nNotes:")
            print("- Baseline workload: 458 rules, 321 widgets, 40 widgets × 5 props × 15 frames.")
            print("- Baseline write counts: class-change 200 (40×5), class-animation 600 (15×40), hover ~645.")
            print("- Run with PYTHONHASHSEED=0 uv run python -m benchmarks for fixed-hash reproducibility.")
            print("- Offscreen: QT_QPA_PLATFORM=offscreen is set by default in")
            print("  benchmarks/common.py; override if you need a visible window.")


if __name__ == "__main__":
    main()
