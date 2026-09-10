"""Hover event. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import change

NAME = "Hover event"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return change(NAME, kind="hover", warmup=warmup, runs=runs)
