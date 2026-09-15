"""Cold rule matching. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import matching

NAME = "Cold rule matching"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return matching(NAME, cold=True, attrs=False, warmup=warmup, runs=runs)
