"""Run each scenario in a fresh Python process and save comparable results."""

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from benchmarks import (
    bench_attr_change,
    bench_attr_matching,
    bench_class_anim_frames,
    bench_class_change,
    bench_cold_matching,
    bench_hover_change,
    bench_hover_frames,
    bench_initial_eval,
    bench_resize_storm,
    bench_warm_matching,
)
from benchmarks.reporting import Report, comparison_table, environment, read_report, report_json
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
    BenchEntry("hover", bench_hover_change.NAME, bench_hover_change.benchmark),
    BenchEntry("hover_anim", bench_hover_frames.NAME, bench_hover_frames.benchmark),
    BenchEntry("resize", bench_resize_storm.NAME, bench_resize_storm.benchmark),
]


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--filter", default="", help="case-insensitive substring of key or name")
    parser.add_argument("--json", action="store_true", help="print only a JSON report to stdout")
    parser.add_argument("--output", type=Path, help="save a JSON report")
    parser.add_argument("--show", type=Path, help="print a saved JSON report as a table and exit")
    parser.add_argument("--compare", type=Path, help="compare medians with a saved report")
    parser.add_argument("--repeat", type=positive, help="measured samples per scenario")
    parser.add_argument("--warmup", type=nonnegative, help="discarded warmup samples")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress on stderr")
    parser.add_argument("--worker", choices=[entry.key for entry in ALL_BENCHES], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list:
        for entry in ALL_BENCHES:
            print(f"{entry.key:14} {entry.name}")
        return
    if args.show:
        try:
            report = read_report(args.show)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(f"cannot read report: {error}")
        print(format_table(list(report.results.values())))
        print("\nAll timings are milliseconds per workload; writes are from a separate untimed sample.")
        return
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal"}:
        parser.error("use the native Qt platform; remove QT_QPA_PLATFORM=offscreen/minimal")
    if args.worker:
        entry = next(entry for entry in ALL_BENCHES if entry.key == args.worker)
        options: dict[str, int] = {}
        if args.repeat is not None:
            options["runs"] = args.repeat
        if args.warmup is not None:
            options["warmup"] = args.warmup
        result = entry.benchmark(**options)
        print(report_json(Report(environment(), {entry.key: result})))
        return
    selected = [entry for entry in ALL_BENCHES if args.filter.lower() in f"{entry.key} {entry.name}".lower()]
    if not selected:
        parser.error(f"no scenarios match {args.filter!r}")
    try:
        baseline = read_report(args.compare) if args.compare else None
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"cannot read baseline: {error}")
    report = Report({}, {})
    failures: list[str] = []
    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "0")
    for entry in selected:
        if not args.quiet:
            print(f"Running {entry.name} ...", file=sys.stderr, flush=True)
        command = [sys.executable, "-m", "benchmarks", "--worker", entry.key]
        if args.repeat is not None:
            command += ["--repeat", str(args.repeat)]
        if args.warmup is not None:
            command += ["--warmup", str(args.warmup)]
        process = subprocess.run(
            command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True
        )
        if process.stderr:
            print(process.stderr, file=sys.stderr, end="")
        if process.returncode:
            failures.append(entry.key)
            continue
        child = Report.from_json(process.stdout)
        if report.metadata and any(
            report.metadata.get(key) != value for key, value in child.metadata.items() if key != "recorded_at"
        ):
            parser.exit(1, "Code or environment changed during the run; no report saved.\n")
        report.metadata = child.metadata
        report.results.update(child.results)
    if failures:
        # Never save a partial run as a successful baseline.
        parser.exit(1, f"Failed scenarios: {', '.join(failures)}; no report saved.\n")
    comparison = None
    if baseline:
        try:
            comparison = comparison_table(baseline, report)
        except ValueError as error:
            parser.error(str(error))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report_json(report) + "\n", encoding="utf-8")
    if args.json:
        print(report_json(report))
        if comparison:
            print(comparison, file=sys.stderr)
    else:
        print(format_table(list(report.results.values())))
        print("\nAll timings are milliseconds per workload; writes are from a separate untimed sample.")
        if comparison:
            print("\n" + comparison)


if __name__ == "__main__":
    main()
