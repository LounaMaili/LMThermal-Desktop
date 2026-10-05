"""Camera-free asynchronous still validation with obsolete-result protection."""

import threading
from PyQt6.QtCore import QThread, pyqtSignal
from lmtx_reader import load_lmtx


class OfflineLoadWorker(QThread):
    result_available = pyqtSignal()

    def __init__(self, path, token, parent=None):
        super().__init__(parent)
        self.path, self.token = path, token
        self.result = None
        self.stopped = threading.Event()

    def request_stop(self): self.stopped.set()

    def run(self):
        try:
            source, timings = load_lmtx(self.path, cancelled=self.stopped.is_set)
            result = (self.token, source, timings, None)
        except Exception as exc:
            result = (self.token, None, None, str(exc))
        if not self.stopped.is_set():
            self.result = result
            self.result_available.emit()
