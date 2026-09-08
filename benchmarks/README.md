# Benchmarks — qt_css_engine

Baseline workload:

- **458 rules**, **321 widgets**, **40 widgets animating five properties over 15 synthetic frames**
- Noise (`.noise-N`) rules carry `border-top-left-radius` so the resize storm exercises the clamp path
- Offscreen (`QT_QPA_PLATFORM=offscreen`), fixed-hash (`PYTHONHASHSEED=0`) for reproducibility
- Binding: PySide6 (offscreen)

## Running

```bash
# All benchmarks (default 7 runs each, 2 warmup)
uv run python -m benchmarks

# Faster — fewer runs
uv run python -m benchmarks --repeat 2

# Single scenario
uv run python -m benchmarks --filter cold
uv run python -m benchmarks --filter class
uv run python -m benchmarks --filter hover

# JSON output
uv run python -m benchmarks --json > results.json

# Fixed-hash, PySide6 (recommended for CI)
PYTHONHASHSEED=0 QT_API=pyside6 uv run python -m benchmarks

# Quiet — only final table
uv run python -m benchmarks --quiet --repeat 2

# Direct file entry (same as -m)
uv run python benchmarks/run.py --list
```

## Scenarios

| Scenario | What it measures | Baseline |
|---|---|---|
| **Cold rule matching** | `RuleMatcher.matching_rules` for all 321 widgets with caches cleared (`clear_caches`). | ~1.4–1.8 ms |
| **Warm cached matching** | Same sweep with caches hot (second pass). | ~0.04 ms |
| **Initial evaluation** | Polish burst: `ensure_wa_hover` + `seed_active_pseudo` + `evaluate_widget_state(POLISH)` for 321 widgets. Engine construction (rule-index build) is inside the timed section. | ~29–30 ms |
| **Cold attr matching** | Same cold sweep on 498 rules (40 extra `.item-N[active=true]`), half the animated widgets carrying `active=true`. | ~1.5–1.6 ms |
| **Warm attr matching** | Same sweep with caches hot. | ~0.04 ms |
| **Initial attr evaluation** | Same polish burst on the 498-rule attr workload (half active). | ~31–32 ms |
| **Class change** | Toggling `.on` on 40 widgets (5 props each) via `setProperty("class", ...)` + `processEvents`. | ~43–45 ms, **200 writes** (40×5) |
| **Attr change** | Same burst via `[active=true]` (`setProperty("active", True)` on 40 widgets, 5 props each). Directly comparable to class change. | ~42–43 ms, **200 writes** (40×5) |
| **Class-animation frames** | 15 synthetic frames after class change. Each frame advances 40×5 running `QVariantAnimation`s via `setCurrentTime` and processes the batched flush. Trigger-phase deferred work is drained before timing. | ~130–137 ms, **600 writes** (15×40) |
| **Hover-animation frames** | Same 15 frames but driven by `:hover` (pseudo-state injected directly into the widget context; bypasses event routing). | ~112–116 ms, **~645 writes** (approximate) |
| **Resize storm** | Resizing 100 radius-styled noise widgets and handling `on_resize` (border-radius clamp path: queue + forced polish + snap + flush). | ~21–23 ms, **100 writes**, 100 resized |

Baseline:

```
Scenario                  Mean  Median     Min     Max  Stdev  Runs  Extra
----------------------  ------  ------  ------  ------  -----  ----  -----------------------
Cold rule matching        1.58    1.55    1.49    1.77   0.08    10  321w 458r
Warm cached matching      0.05    0.05    0.04    0.06   0.01    10  321w 458r
Initial evaluation       28.65   28.60   28.31   29.16   0.26    10  321w 458r
Cold attr matching        1.54    1.55    1.43    1.67   0.07     7  321w 498r
Warm attr matching        0.04    0.04    0.04    0.05   0.00    15  321w 498r
Initial attr evaluation  31.74   31.65   31.46   32.16   0.22     7  321w 498r
Class change             39.78   39.82   38.73   41.10   0.72    10  200 writes
Attr change              42.56   42.61   41.76   43.56   0.63     7  200 writes
Class-animation frames  129.34  130.22  119.01  138.24   4.93    10  600 writes
Hover-animation frames  106.20  104.56  101.36  112.50   4.32    10  611 writes
Resize storm             19.99   19.90   19.77   20.43   0.20    10  100 writes, 100 resized
```

`Extra` is an inline column (`writes`, `321w 458r`, `resized`). Baseline write counts confirm the batching:

- Class-change: **200** (40×5)
- Attr-change: **200** (40×5) — same props driven through `[active=true]` instead of `.on`
- Class-animation: **600** (15×40)
- Hover: **~611** (15×40 plus animation-completion flushes; approximate — see below)
- Resize storm: **100** (one snap + flush per resized widget)

Frame write counts are approximate, unlike the exact class-change count: real-timer
animation-completion callbacks race the synthetic `setCurrentTime` ticks, so class/hover
frames wander around the model from run to run. The resize storm reports `writes`
alongside `resized`.

## Layout

```
benchmarks/
  __main__.py              # single entry point (uv run python -m benchmarks)
  run.py                   # alias (uv run python benchmarks/run.py)
  common.py                # heavy stylesheet + widget hierarchy + write counters
  runner.py                # timing helpers, table formatting
  bench_cold_matching.py
  bench_warm_matching.py
  bench_initial_eval.py
  bench_attr_matching.py   # three scenarios: cold / warm / initial with attrs
  bench_class_change.py
  bench_attr_change.py
  bench_class_anim_frames.py
  bench_hover_frames.py
  bench_resize_storm.py
```

Each `bench_*.py` exposes `NAME` and `benchmark(warmup, runs) -> BenchResult`
(`bench_attr_matching.py` exposes three: cold / warm / initial). The runner imports them via `ALL_BENCHES` in `__main__.py`.

## Notes

- Offscreen is forced in `benchmarks/common.py` via `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` before any Qt import. Override by setting the variable before running if you need a visible window.
- Timings are medians over several runs with `gc.collect()` between runs. Per-frame animation benchmarks (class/hover) use 5 runs by default because they are heavier (each run builds a fresh 321-widget tree).
- For CI, the suite should stay under a few seconds with `--repeat 2`.
