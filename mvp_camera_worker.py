"""Qt worker owning HT-301 discovery, acquisition and the validated session."""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ht301_camera import discover_device, open_camera
from radiometric_sequence_diagnostic import ZoomControl
from radiometric_session import HT301RadiometricSession, SessionState
from radiometric_session_diagnostic import LatestFrameStream


class WorkerStopped(Exception):
    """Stop an in-progress initialization without sending further controls."""


class CameraWorker(QThread):
    """Own camera handles off the GUI thread and coalesce observations for Qt."""

    frame_available = pyqtSignal()
    connected = pyqtSignal(str)
    notice = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_requested = threading.Event()
        self._initialize_requested = threading.Event()
        self._lock = threading.Lock()
        self._latest = None
        self._signal_pending = False

    def request_initialize(self) -> None:
        """Queue the user's supported initialization request without blocking Qt."""
        self._initialize_requested.set()

    def request_stop(self) -> None:
        """Ask the acquisition loop to release its camera and control handles."""
        self._stop_requested.set()

    def take_latest(self):
        """Consume only the newest observation, bounding the UI event queue."""
        with self._lock:
            observation = self._latest
            self._latest = None
            self._signal_pending = False
        return observation

    def _publish(self, observation) -> None:
        """Replace stale observations while preserving session processing order."""
        if self._stop_requested.is_set():
            raise WorkerStopped()
        with self._lock:
            self._latest = observation
            emit = not self._signal_pending
            self._signal_pending = True
        if emit:
            self.frame_available.emit()

    def run(self) -> None:
        """Keep all V4L2 reads, control writes and lookup work outside Qt's GUI thread."""
        capture = control = stream = None
        try:
            device = discover_device()
            capture = open_camera(device)
            control = ZoomControl(device)
            stream = LatestFrameStream(capture)
            self.connected.emit("Infiray HT-301 / T3-317-13")
            session = HT301RadiometricSession(stream, control, on_observation=self._publish)
            while not self._stop_requested.is_set():
                if self._initialize_requested.is_set():
                    self._initialize_requested.clear()
                    if session.state != SessionState.DISPLAY_STREAM:
                        self.notice.emit("Initialize only from display mode; unknown raw14 host state is read-only.")
                    else:
                        self.notice.emit("Initializing radiometric mode")
                        session.initialize_normal_range()
                else:
                    session.poll()
        except WorkerStopped:
            pass
        except Exception as exc:
            if not self._stop_requested.is_set():
                self.failed.emit(str(exc))
        finally:
            safe_release = stream is None or stream.close()
            if control is not None:
                control.close()
            if capture is not None and safe_release:
                capture.release()
