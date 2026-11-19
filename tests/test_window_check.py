"""The opt-in qualification path uses existing viewers, preserves sources and closes its own workers."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication,QWidget
from PyQt6.QtCore import QTimer
from desktop_window_check import run,pointer_probe
from lmthermal_desktop import DesktopWorkspace


class WindowCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_native_input_after_probe_cannot_relabel_its_synchronous_result(self):
        class Image(QWidget):
            """A later legitimate cursor input must remain observable, without corrupting the probe receipt."""
            pointer=None
            def mouseMoveEvent(self,event):self.pointer=(int(event.position().x()),int(event.position().y()))
        image=Image();image.setMouseTracking(True)
        QTimer.singleShot(0,lambda:setattr(image,'pointer',(9,9)))
        self.assertEqual((0,0),pointer_probe(self.app,image,(0.5,0.5),(0,0)))
        self.app.processEvents()
        self.assertEqual((9,9),image.pointer)
        image.close()
    def test_verified_masked_source_drives_readings_png_metadata_gap_and_normal_close(self):
        source=Path(__file__).parent/'fixtures/lmtr/files/illustration-01-stored.lmtr'
        with TemporaryDirectory() as name,patch('lmthermal_viewer.CameraWorker',side_effect=AssertionError('camera')):
            window=DesktopWorkspace();window.open_path(source);window.show()
            report=run(self.app,window,source,Path(name)/'report')
            self.assertEqual('passed',report['status'],report.get('error'))
            self.assertTrue(report['closed_normally']);self.assertFalse(window._documents)
            self.assertEqual(report['source_before'],report['source_after'])
            self.assertIn('mean 25',report['roi_text'])
    def test_explicit_negative_case_preserves_visible_refusal_and_no_stale_frame(self):
        source=Path(__file__).parent/'fixtures/lmtx/corrupt-sha.lmtx'
        with TemporaryDirectory() as name,patch('lmthermal_viewer.CameraWorker',side_effect=AssertionError('camera')):
            window=DesktopWorkspace();window.open_path(source);window.show()
            report=run(self.app,window,source,Path(name)/'report',expect_error=True)
            self.assertEqual('passed',report['status'],report.get('error'))
            self.assertEqual(['visible_error_without_stale_image'],report['checks'])
            self.assertTrue(report['closed_normally'])
