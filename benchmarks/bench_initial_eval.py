"""Initial evaluation. See benchmarks/README.md for the timing boundary."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import initial

NAME = "Initial evaluation"


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return initial(NAME, attrs=False, warmup=warmup, runs=runs)
