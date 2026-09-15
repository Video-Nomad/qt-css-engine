"""Versioned reports and guarded before/after comparisons."""

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

from benchmarks.runner import BenchResult

SCHEMA_VERSION = 2


class ResultJSON(TypedDict):
    name: str
    mean_ms: float
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    runs: int
    samples_ms: list[float]
    warmup: int
    extra: dict[str, object] | None


class ReportJSON(TypedDict):
    schema_version: int
    metadata: dict[str, str]
    results: dict[str, ResultJSON]


@dataclass
class Report:
    metadata: dict[str, str]
    results: dict[str, BenchResult]

    @classmethod
    def from_json(cls, text: str) -> Report:
        raw: object = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("Incompatible benchmark report; record a new baseline using this suite")
        data = cast(ReportJSON, raw)
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Incompatible benchmark report; record a new baseline using this suite")
        return cls(data["metadata"], {key: BenchResult(**value) for key, value in data["results"].items()})


def report_json(report: Report) -> str:
    return json.dumps({"schema_version": SCHEMA_VERSION, **asdict(report)}, indent=2)


def read_report(path: Path) -> Report:
    return Report.from_json(path.read_text(encoding="utf-8"))


def environment() -> dict[str, str]:
    from benchmarks.common import get_app
    from qt_css_engine.qt_compat._api import USE_PYSIDE6
    from qt_css_engine.qt_compat.QtCore import qVersion

    app = get_app()
    binding = "PySide6" if USE_PYSIDE6 else "PyQt6"
    directory = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_text(encoding="utf-8").encode())
    engine_digest = hashlib.sha256()
    engine_directory = directory.parent / "qt_css_engine"
    for path in sorted(engine_directory.rglob("*.py")):
        engine_digest.update(path.relative_to(engine_directory).as_posix().encode())
        engine_digest.update(path.read_text(encoding="utf-8").encode())

    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=directory.parent, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    style = app.style()
    screen = app.primaryScreen()
    metadata = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "revision": git("rev-parse", "HEAD"),
        "dirty": str(bool(git("status", "--porcelain", "--untracked-files=no"))),
        "suite_sha256": digest.hexdigest(),
        "engine_sha256": engine_digest.hexdigest(),
        "python": sys.version,
        "os": platform.platform(),
        "machine": platform.machine(),
        "host": platform.node(),
        "processor": platform.processor(),
        "binding": binding,
        "binding_version": importlib.metadata.version(binding),
        "qt": qVersion() or "unknown",
        "tinycss2": importlib.metadata.version("tinycss2"),
        "platform_plugin": app.platformName(),
        "style": style.objectName() if style is not None else "none",
        "font": app.font().toString(),
        "dpi": str(screen.logicalDotsPerInch()) if screen is not None else "none",
        "device_pixel_ratio": str(screen.devicePixelRatio()) if screen is not None else "none",
        "hash_seed": os.environ.get("PYTHONHASHSEED", "random"),
        "gc": "collect before each sample; disabled during timing",
    }
    for key in ("QT_SCALE_FACTOR", "QT_STYLE_OVERRIDE", "CSS_ENGINE_EVENT_LOGGING", "QT_LOGGING_RULES"):
        metadata[key] = os.environ.get(key, "")
    return metadata


def comparison_table(before: Report, after: Report) -> str:
    ignored = {"recorded_at", "revision", "dirty", "engine_sha256"}
    changed = sorted(
        key
        for key in before.metadata.keys() | after.metadata.keys()
        if key not in ignored and before.metadata.get(key) != after.metadata.get(key)
    )
    if changed:
        raise ValueError(f"Incompatible comparison metadata: {', '.join(changed)}. Record a comparable baseline.")
    lines = [
        "Median comparison (negative = faster; positive = slower)",
        f"{'Scenario':28} {'Before ms':>10} {'After ms':>10} {'Change':>10}",
    ]
    for key, result in after.results.items():
        old = before.results.get(key)
        if old is None:
            raise ValueError(f"Baseline has no scenario {key!r}")
        old_work = {k: v for k, v in (old.extra or {}).items() if k != "diagnostic_writes"}
        new_work = {k: v for k, v in (result.extra or {}).items() if k != "diagnostic_writes"}
        if old_work != new_work or old.runs != result.runs or old.warmup != result.warmup:
            raise ValueError(f"Workload or sampling settings changed for {key!r}")
        delta = f"{(result.median_ms / old.median_ms - 1) * 100:+.1f}%" if old.median_ms > 0 else "n/a"
        lines.append(f"{result.name:28} {old.median_ms:10.3f} {result.median_ms:10.3f} {delta:>10}")
    lines.append("Small changes within the sample spread are inconclusive; repeat the comparison.")
    return "\n".join(lines)
