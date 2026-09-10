"""Hover-animation frames. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import frames

NAME = "Hover-animation frames"


def benchmark(*, warmup: int = 1, runs: int = 5) -> BenchResult:
    return frames(NAME, kind="hover", warmup=warmup, runs=runs)
