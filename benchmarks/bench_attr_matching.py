"""Matching includes the live attribute predicates used by the cascade."""

from benchmarks.runner import BenchResult
from benchmarks.workloads import initial, matching

NAME_COLD = "Cold attr matching"
NAME_WARM = "Warm attr matching"
NAME_INITIAL = "Initial attr evaluation"
NAME = NAME_COLD


def benchmark_cold(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return matching(NAME_COLD, cold=True, attrs=True, warmup=warmup, runs=runs)


def benchmark_warm(*, warmup: int = 3, runs: int = 15) -> BenchResult:
    return matching(NAME_WARM, cold=False, attrs=True, warmup=warmup, runs=runs)


def benchmark_initial(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return initial(NAME_INITIAL, attrs=True, warmup=warmup, runs=runs)


def benchmark(*, warmup: int = 2, runs: int = 7) -> BenchResult:
    return benchmark_cold(warmup=warmup, runs=runs)
