"""Background jobs shared by the desktop interface."""
from PySide6.QtCore import QObject, QRunnable, Signal

class Signals(QObject):
    progress = Signal(str, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, Signals()

    def run(self):
        try:
            self.signals.result.emit(self.fn())
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()
