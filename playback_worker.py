"""One coalesced request/result slot for offline validation and decompression."""

import threading
from PyQt6.QtCore import QThread, pyqtSignal
from radiometric_playback import PlaybackModel


class PlaybackWorker(QThread):
    result_available = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._pending = self._result = None
        self._signalled = False
        self._stopping = False

    def request(self, token, *, path=None, model=None, index=0):
        with self._condition:
            self._pending = (token, path, model, index)
            self._condition.notify()

    def take_latest(self):
        with self._condition:
            result, self._result = self._result, None
            self._signalled = False
            return result

    def request_stop(self):
        with self._condition:
            self._stopping = True
            self._pending = None
            self._condition.notify()

    def run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                token, path, model, index = self._pending
                self._pending = None
            frame = error = None
            try:
                if path is not None:
                    model = PlaybackModel.open(path)
                frame = model.frame(index) if model.entries else None
            except Exception as exc:
                error = str(exc)
            with self._condition:
                if self._stopping:
                    return
                # An obsolete scrub result never overwrites a newer pending request.
                if self._pending is not None:
                    continue
                self._result = (token, model, index, frame, error)
                emit = not self._signalled
                self._signalled = True
            if emit:
                self.result_available.emit()
