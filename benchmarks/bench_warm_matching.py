"""Warm cached matching. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import matching

NAME = "Warm cached matching"


def benchmark(*, warmup: int = 3, runs: int = 15) -> BenchResult:
    return matching(NAME, cold=False, attrs=False, warmup=warmup, runs=runs)
