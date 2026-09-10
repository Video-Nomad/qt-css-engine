"""Resize storm. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import resize

NAME = "Resize storm"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return resize(NAME, warmup=warmup, runs=runs)
