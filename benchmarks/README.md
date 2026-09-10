# Performance comparisons

Run from the repository root in PowerShell. Pin the binding, then save a baseline
**using this version of the benchmarks before changing the engine**:

```powershell
$env:QT_API = "pyqt6"  # or pyside6; keep it the same for both runs
uv run python -m benchmarks --output .benchmarks/before.json

# Make the engine change, then run:
uv run python -m benchmarks --compare .benchmarks/before.json --output .benchmarks/after.json
```

Negative median changes mean faster; positive changes mean slower. Compare the
sample spread too. Small changes within the run-to-run variation are inconclusive.
For a closer look, use `--repeat 15 --warmup 3` on **both** runs and repeat the
before/after experiment. Use the same machine, power mode, dependencies and idle
background workload. Run timings without other test suites or profilers running.

`--filter class` selects a subset; `--list` lists keys. A filtered run can compare
against a full baseline. `--repeat 1 --warmup 0` is a smoke check, not a reliable
performance measurement. `--json` emits a complete JSON document on stdout;
progress and Qt diagnostics go to stderr. `.benchmarks/` is ignored by Git.

Display a saved report as the usual results table without running benchmarks:

```powershell
uv run python -m benchmarks --show .benchmarks/after.json
```

## Measurement boundaries

The fixture has 321 widgets and 458 rules (498 for attribute cases). It includes
40 buttons, nested wrapper/container branches and radius-styled noise labels.
The cleaned static QSS is applied to the root, alongside the engine rules, so
style writes and repolishing exercise Qt's stylesheet machinery too.

| Key | Timed work |
| --- | --- |
| `cold` | One matching pass over all 321 widgets with empty widget/identity caches; rule compilation/index construction and cache clearing are excluded. |
| `warm` | Matching with primed caches; 100 passes per sample, reported as milliseconds per **one 321-widget pass**. |
| `attr_cold`, `attr_warm` | Matching plus live attribute predicates, as used by the cascade. Half the 40 buttons have `active=true`. Warm samples use 100 passes and the same per-pass normalization. |
| `initial`, `attr_initial` | New engine/index construction, 321 delivered Polish events, and queued engine work until settled. Each sample has a fresh tree with static QSS already installed. CSS parsing, tree creation, native first show, and destruction are excluded. |
| `class_change`, `attr` | A synchronous burst of 40 property changes through Qt's event filter, including repolish and creation of 200 animations. Deferred flushes, layout, painting and animation ticks are excluded. |
| `hover` | 40 delivered `QHoverEvent` enters through the event filter, including creation of 200 animations. Like the property-change cases, deferred work is excluded. |
| `class_anim`, `hover_anim` | 15 synthetic frames at 20–300 ms for 40 × 5 animations, including deferred style/layout work and final completion callbacks. Trigger/setup work is excluded. Results are per **15-frame sequence**, not FPS or per-frame latency. |
| `resize` | One actual resize on each of 100 labels, from 120×30 to 122×34, plus queued clamp/style work. Layouts are frozen beforehand so they cannot undo or multiply the resizes. Each radius must change from 15 to 17 px. |

These are fixed workload regression benchmarks, not exhaustive engine coverage.
They do not currently measure parsing, hot reload, reparenting, effects, or
real-time paint/compositor throughput. Class and attribute scenarios also have
different rule counts; compare each scenario to itself across engine revisions.

## Repeatability and correctness

- Each scenario runs in a fresh Python process. `PYTHONHASHSEED` defaults to `0`
  for the worker processes, or preserves your explicitly supplied seed.
- Setup, teardown, correctness checks and garbage collection are outside timing.
  Cyclic GC is disabled during each timed sample and its previous state restored.
  Samples have fresh trees/engines; warm matcher caches are deliberately primed.
- Animation clocks are paused at time zero before processing any queued events.
  Every synthetic frame seeks all 200 animations explicitly; the wall clock cannot
  advance them. The last frame includes completion, so write counts need not be
  exactly 15 × 40. Write counts are diagnostics from a separate untimed sample.
- Windows use the native Qt platform with `WA_DontShowOnScreen`, avoiding desktop
  focus/mouse interference. The CLI rejects `offscreen` and `minimal` platforms.
  No global Qt message handler suppresses errors.
- Assertions verify fixture sizes, matcher results against an uncached scan,
  attribute hits/misses, five running properties per button, final animation
  values and resize clamp changes. Errors fail the run; partial suites are never
  saved as successful baselines. Run Python normally, without `-O`.
- JSON retains every sample, summary statistics, warmup counts, workload sizes,
  source revision/dirty flag, engine and benchmark source hashes, Python/Qt/binding
  versions, platform, style, font, DPI and hash seed. Comparisons reject changed
  benchmark code, environment or sampling settings. Engine code/revision changes
  are expected. Metadata cannot detect every source of system noise.
