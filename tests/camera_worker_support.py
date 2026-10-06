"""Camera-free Qt signal source for UI lifecycle tests on every host."""

from PyQt6.QtCore import QThread, pyqtSignal


class CameraWorkerStub(QThread):
    """Supply observations without importing Linux acquisition or opening hardware."""

    frame_available = pyqtSignal()
    connected = pyqtSignal(str)
    notice = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._latest = None

    def request_stop(self):
        pass

    def request_initialize(self):
        pass

    def take_latest(self):
        latest, self._latest = self._latest, None
        return latest

    def _publish(self, observation):
        self._latest = observation
        self.frame_available.emit()
