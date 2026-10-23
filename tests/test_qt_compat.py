"""Exercise lazy Qt compatibility exports in isolated interpreters."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.mark.parametrize("qt_api", ["pyqt6", "pyside6"])
def test_compat_exports_are_lazy_and_preserve_binding_objects(qt_api: str) -> None:
    package = {"pyqt6": "PyQt6", "pyside6": "PySide6"}[qt_api]
    if importlib.util.find_spec(package) is None:
        pytest.skip(f"{qt_api} not installed")

    env = os.environ.copy()
    env["QT_API"] = qt_api
    if qt_api == "pyside6":
        env["PYSIDE6_OPTION_LAZY"] = "1"

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            dedent(
                """
                import importlib

                from qt_css_engine.qt_compat import QtCore, QtGui, QtWidgets

                binding = "PySide6" if importlib.import_module("qt_css_engine.qt_compat._api").USE_PYSIDE6 else "PyQt6"
                core = importlib.import_module(f"{binding}.QtCore")
                gui = importlib.import_module(f"{binding}.QtGui")
                widgets = importlib.import_module(f"{binding}.QtWidgets")

                if binding == "PySide6":
                    assert "QCoreApplication" not in vars(core)
                    assert "QPdfWriter" not in vars(gui)
                    assert "QWidget" not in vars(widgets)

                from qt_css_engine.qt_compat.QtCore import QEasingCurve, QObject, QVariantAnimation, Signal, Slot
                from qt_css_engine.qt_compat.QtGui import QColor
                from qt_css_engine.qt_compat.QtWidgets import QWidget

                assert QEasingCurve is core.QEasingCurve
                assert QObject is core.QObject
                assert QVariantAnimation is core.QVariantAnimation
                assert QColor is gui.QColor
                assert QWidget is widgets.QWidget
                assert QtCore.QEasingCurve is QEasingCurve
                assert QtCore.QObject is QObject
                assert QtCore.QVariantAnimation is QVariantAnimation
                assert QtGui.QColor is QColor
                assert QtWidgets.QWidget is QWidget

                signal_name, slot_name = (
                    ("Signal", "Slot") if binding == "PySide6" else ("pyqtSignal", "pyqtSlot")
                )
                assert Signal is getattr(core, signal_name)
                assert Slot is getattr(core, slot_name)
                assert QtCore.Signal is Signal
                assert QtCore.Slot is Slot

                missing_name = "__qt_compat_symbol_that_does_not_exist__"
                for module in (QtCore, QtGui, QtWidgets):
                    try:
                        getattr(module, missing_name)
                    except AttributeError:
                        pass
                    else:
                        raise AssertionError(f"missing attribute unexpectedly resolved: {module.__name__}")

                try:
                    exec("from qt_css_engine.qt_compat.QtCore import __qt_compat_symbol_that_does_not_exist__")
                except ImportError:
                    pass
                else:
                    raise AssertionError("from-import of a missing symbol unexpectedly succeeded")
                """
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=30,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
