"""The opt-in qualification path uses existing viewers, preserves sources and closes its own workers."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from desktop_window_check import run
from lmthermal_desktop import DesktopWorkspace


class WindowCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
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
