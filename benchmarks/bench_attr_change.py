"""Attr change. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import change

NAME = "Attr change"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return change(NAME, kind="attr", warmup=warmup, runs=runs)
