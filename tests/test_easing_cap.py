"""Use subprocesses to isolate the bindings' custom callback registries."""

import importlib.util
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

import pytest

from qt_css_engine.qt_compat.QtCore import QEasingCurve
from qt_css_engine.utils import easing


def _run_child(script: str, qt_api: str) -> None:
    if importlib.util.find_spec({"pyqt6": "PyQt6", "pyside6": "PySide6"}[qt_api]) is None:
        pytest.skip(f"{qt_api} not installed")
    env = os.environ.copy()
    env["QT_API"] = qt_api
    proc = subprocess.run(
        [sys.executable, "-c", dedent(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


@pytest.mark.parametrize("qt_api", ["pyqt6", "pyside6"])
def test_steps_cache_reuses_normalized_positions(qt_api: str) -> None:
    _run_child(
        """
        from qt_css_engine.qt_compat.QtCore import QEasingCurve
        from qt_css_engine.utils.easing import make_steps_curve

        for position, expected in (("start", 0.5), ("end", 0.25)):
            first = make_steps_curve(4, position)
            for _ in range(20):
                for alias in (position, position.upper(), "jump-" + position, "JUMP-" + position.upper()):
                    assert make_steps_curve(4, alias) is first
            assert first.type() == QEasingCurve.Type.Custom
            assert first.valueForProgress(0.37) == expected
        """,
        qt_api,
    )


@pytest.mark.parametrize("qt_api", ["pyqt6", "pyside6"])
def test_cached_steps_curve_works_in_many_animations(qt_api: str) -> None:
    _run_child(
        """
        from qt_css_engine.qt_compat.QtCore import QEasingCurve, QVariantAnimation
        from qt_css_engine.utils.easing import make_steps_curve

        curves = [make_steps_curve(n, "end") for n in range(1, 11)]
        animations = []
        for _ in range(20):
            animation = QVariantAnimation()
            animation.setDuration(1000)
            animation.setEasingCurve(curves[3])
            animation.setStartValue(0.0)
            animation.setEndValue(100.0)
            animation.setCurrentTime(370)
            animations.append(animation)
        assert all(animation.easingCurve().type() == QEasingCurve.Type.Custom for animation in animations)
        assert all(animation.currentValue() == 25.0 for animation in animations)
        """,
        qt_api,
    )


@pytest.mark.parametrize("qt_api", ["pyqt6", "pyside6"])
def test_new_steps_curves_fall_back_to_linear_after_ten_callbacks(qt_api: str) -> None:
    _run_child(
        """
        import math

        from qt_css_engine.qt_compat.QtCore import QEasingCurve
        from qt_css_engine.utils.easing import make_steps_curve

        curves = [make_steps_curve(n, "end") for n in range(1, 13)]
        for n, curve in enumerate(curves, start=1):
            expected_type = QEasingCurve.Type.Custom if n <= 10 else QEasingCurve.Type.Linear
            assert curve.type() == expected_type, (n, curve.type())
            for progress in (0.0, 0.37, 0.99, 1.0):
                expected = math.floor(progress * n) / n if n <= 10 else progress
                assert curve.valueForProgress(progress) == expected, (n, progress)
            assert make_steps_curve(n, "JUMP-END") is curve
        """,
        qt_api,
    )


def test_pyqt6_counts_callbacks_registered_outside_the_engine() -> None:
    _run_child(
        """
        from qt_css_engine.qt_compat.QtCore import QEasingCurve
        from qt_css_engine.utils.easing import make_steps_curve

        external_curves = []
        for _ in range(2):
            curve = QEasingCurve()
            curve.setCustomType(lambda progress: progress * progress)
            external_curves.append(curve)
        curves = [make_steps_curve(n, "end") for n in range(1, 11)]
        assert all(curve.type() == QEasingCurve.Type.Custom for curve in curves[:8])
        assert all(curve.type() == QEasingCurve.Type.Linear for curve in curves[8:])
        assert all(curve.valueForProgress(0.37) == 0.37 for curve in curves[8:])
        assert all(curve.valueForProgress(0.5) == 0.25 for curve in external_curves)
        """,
        "pyqt6",
    )


@pytest.mark.parametrize(
    ("use_pyside6", "error_type"), [(False, RuntimeError), (True, RuntimeError), (True, ValueError)]
)
def test_steps_curve_propagates_unhandled_registration_errors(
    monkeypatch: pytest.MonkeyPatch, use_pyside6: bool, error_type: type[Exception]
) -> None:
    def fail_registration(_curve: QEasingCurve, _callback: Callable[[float], float]) -> None:
        raise error_type("registration failed")

    monkeypatch.setattr(easing, "USE_PYSIDE6", use_pyside6)
    monkeypatch.setattr(easing, "_steps_curve_cache", {})
    monkeypatch.setattr(QEasingCurve, "setCustomType", fail_registration)
    with pytest.raises(error_type, match="registration failed"):
        easing.make_steps_curve(4, "end")
