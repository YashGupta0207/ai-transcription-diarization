"""Qt-safe execution of blocking desktop I/O outside the GUI thread."""
from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class _Runnable(QRunnable):
    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


def run_in_background(fn: Callable[..., Any], *args, **kwargs) -> WorkerSignals:
    """Schedule blocking work; all returned signals are delivered to the GUI thread."""
    runnable = _Runnable(fn, args, kwargs)
    QThreadPool.globalInstance().start(runnable)
    return runnable.signals
