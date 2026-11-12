"""Product workspace owns existing readers; it never opens hardware just to show navigation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import hashlib
import time
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication,QWidget
from PyQt6.QtCore import pyqtSignal
from lmthermal_desktop import DesktopWorkspace,MAX_DOCUMENTS,self_check

FILES=Path(__file__).parent/'fixtures/lmtr/files'


class FakeView(QWidget):
    closed=pyqtSignal()
    def __init__(self):super().__init__();self.routes=[]
    def open_lmtx(self,path):self.routes.append(('lmtx',path))
    def open_capture(self,path):self.routes.append(('capture',path))
    def open_recording(self,path):self.routes.append(('legacy-recording',path))
    def closeEvent(self,event):event.accept();self.closed.emit()


class DesktopWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.window=DesktopWorkspace();self.window.show()
    def wait(self,condition):
        until=time.monotonic()+10
        while not condition() and time.monotonic()<until:self.app.processEvents();time.sleep(.002)
        self.assertTrue(condition())
    def tearDown(self):
        self.window.close();self.wait(lambda:not self.window._documents);self.app.processEvents()
    def test_home_and_live_workspace_require_explicit_connect(self):
        with patch('lmthermal_viewer.CameraWorker',side_effect=AssertionError('implicit camera')):
            self.assertEqual({},self.window._documents)
            first=self.window.open_live();second=self.window.open_live()
            self.app.processEvents()
            self.assertIs(first,second);self.assertIsNone(first.worker)
            self.assertFalse(first._auto_connect_timer.isActive())
    def test_lmtr_source_uses_reviewed_async_reader_and_remains_exact(self):
        path=FILES/'illustration-01-stored.lmtr';before=hashlib.sha256(path.read_bytes()).hexdigest()
        viewer=self.window.open_path(path)
        self.wait(lambda:viewer.state is not None)
        viewer._request('seek',1);self.wait(lambda:not viewer._in_flight)
        self.assertEqual(30,viewer.frame.point(1,0))
        self.assertIsNone(self.window._live)
        self.window.close_document(self.window.tabs.indexOf(viewer))
        self.wait(lambda:not self.window._documents)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
    def test_format_routes_reuse_distinct_existing_apis(self):
        self.window._legacy_factory=FakeView
        for name,expected,recording in [('still.lmtx','lmtx',False),('legacy.json','capture',False),
                                        ('scene.lmthermal','legacy-recording',True)]:
            viewer=self.window.open_path(name,legacy_recording=recording)
            self.assertEqual(expected,viewer.routes[0][0])
    def test_document_count_and_home_close_are_bounded(self):
        self.window._legacy_factory=FakeView
        for n in range(MAX_DOCUMENTS):self.window.open_path(f'{n}.json')
        with patch('lmthermal_desktop.QMessageBox.information') as prompt:
            self.assertIsNone(self.window.open_path('extra.json'));prompt.assert_called_once()
        self.window.close_document(0)
        self.assertEqual(MAX_DOCUMENTS,len(self.window._documents))
    def test_application_close_waits_for_owned_lmtr_worker(self):
        viewer=self.window.open_path(FILES/'illustration-01-stored.lmtr')
        self.wait(lambda:viewer.state is not None)
        self.window.close()
        self.wait(lambda:not self.window._documents)
        self.assertFalse(viewer.worker.isRunning())
    def test_runtime_self_check_uses_no_camera_and_loads_frozen_schema(self):
        with patch('lmthermal_viewer.CameraWorker',side_effect=AssertionError('implicit camera')):
            report=self_check()
        self.assertEqual('passed',report['status']);self.assertFalse(report['camera_opened'])
    def test_parent_close_does_not_block_or_delete_a_busy_lmtr_worker(self):
        import threading
        from PyQt6.QtCore import QTimer
        from lmtr_reader import LmtrPlaybackModel
        viewer=self.window.open_path(FILES/'illustration-01-stored.lmtr')
        self.wait(lambda:viewer.state is not None)
        entered=threading.Event();release=threading.Event();original=LmtrPlaybackModel.frame
        def blocked(model,*args,**kwargs):
            entered.set();release.wait(2);return original(model,*args,**kwargs)
        try:
            with patch.object(LmtrPlaybackModel,'frame',blocked):
                viewer._request('seek',1);self.wait(entered.is_set)
                QTimer.singleShot(30,release.set)
                start=time.monotonic();self.window.close()
                self.assertLess(time.monotonic()-start,.25)
                self.assertTrue(self.window._documents)
                self.wait(lambda:not self.window._documents)
        finally:release.set()

    def test_closing_legacy_view_does_not_consume_queued_camera_frames(self):
        from unittest.mock import Mock
        viewer=self.window.open_live()
        viewer._workspace_closing=True
        worker=Mock();viewer.worker=worker
        try:
            viewer._on_frame_available()
            worker.take_latest.assert_not_called()
            self.assertIsNone(viewer.observation)
        finally:viewer.worker=None
