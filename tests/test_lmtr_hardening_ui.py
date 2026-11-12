"""Deterministic file-generation, immutable export and bounded worker lifetime checks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from lmtr_viewer import LmtrWindow, LmtrWorker
from lmtr_reader import LmtrPlaybackModel

ROOT=Path(__file__).parent/'fixtures/lmtr/files'


class LmtrHardeningUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self): self.window=LmtrWindow();self.window.show();self.gates=[]
    def tearDown(self):
        for gate in self.gates: gate.set()
        self.window.close()
        self.wait(lambda:not self.window.worker.isRunning())
        self.app.processEvents()
    def wait(self, condition):
        deadline=time.monotonic()+8
        while not condition() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(condition(),self.window.status.text())
    def opened(self):
        self.window.open_path(ROOT/'illustration-01-stored.lmtr')
        self.wait(lambda:self.window.state is not None)
        self.window._request('seek',1)
        self.wait(lambda:self.window.frame is not None)
    def test_png_uses_frame_and_presentation_frozen_before_modal_destination(self):
        self.opened();w=self.window
        expected=w.frame.render(w.palette.currentText(),w.image.effective_bounds)
        with TemporaryDirectory() as directory:
            target=Path(directory)/'frozen.png'
            def destination(*args):
                # Nested Qt event loop may advance playback to a blank event.
                w._request('seek',0)
                self.wait(lambda:not w._in_flight)
                self.assertIsNone(w.frame)
                w.palette.setCurrentText('Turbo')
                return str(target),''
            with patch('lmtr_viewer.QFileDialog.getSaveFileName',side_effect=destination): w._save()
            image=cv2.cvtColor(cv2.imread(str(target)),cv2.COLOR_BGR2RGB)
            np.testing.assert_array_equal(expected,image)
    def test_navigation_cannot_replace_a_pending_open_of_a_different_source(self):
        self.opened();w=self.window
        entered=threading.Event();release=threading.Event();self.gates.append(release)
        original=LmtrPlaybackModel.frame
        def blocked(model,*args,**kwargs):
            if model.path.name=='illustration-01-stored.lmtr' and not entered.is_set():
                entered.set();self.assertTrue(release.wait(5))
            return original(model,*args,**kwargs)
        with patch.object(LmtrPlaybackModel,'frame',blocked):
            w._request('verify')
            self.wait(entered.is_set)
            target=ROOT/'illustration-02-stored.lmtr'
            w.open_path(target)
            w._request('seek',1)  # Old slider input must not supersede opening target.
            release.set()
            self.wait(lambda:w.state is not None)
            w._request('seek',1)
            self.wait(lambda:not w._in_flight and w.frame is not None)
            self.assertEqual(target.absolute(),w.frame.source_path)
    def test_stop_releases_unconsumed_result_and_pending_references(self):
        worker=LmtrWorker()
        worker.result=(1,object(),{},None);worker.pending=(2,'open',object())
        worker.stop()
        self.assertIsNone(worker.result);self.assertIsNone(worker.pending)
    def test_close_is_asynchronous_and_releases_owner_after_cooperative_cancel(self):
        self.opened();w=self.window
        entered=threading.Event();release=threading.Event();self.gates.append(release)
        original=LmtrPlaybackModel.frame
        def blocked(model,*args,**kwargs):
            entered.set();release.wait(2)
            return original(model,*args,**kwargs)
        with patch.object(LmtrPlaybackModel,'frame',blocked):
            w._request('seek',1);self.wait(entered.is_set)
            QTimer.singleShot(40,release.set)
            start=time.monotonic();w.close();elapsed=time.monotonic()-start
            self.assertLess(elapsed,.25,'Close must not block the GUI waiting for a worker')
            self.wait(lambda:not w.worker.isRunning())
            self.app.processEvents()
            self.assertIsNone(w.frame);self.assertIsNone(w.state)
            self.assertIsNone(w.image.observation)
    def test_gap_clears_image_legend_readings_and_disables_new_roi(self):
        from lmtr_test_support import gap_plan
        from lmtr_reference.container import write_plan
        with TemporaryDirectory() as temp:
            path=Path(temp)/'gap.lmtr';write_plan(gap_plan(),path)
            w=self.window;w.open_path(path);self.wait(lambda:w.state is not None)
            w._request('seek',1);self.wait(lambda:not w._in_flight)
            w._request('seek',2);self.wait(lambda:not w._in_flight)
            self.assertIsNone(w.frame);self.assertIsNone(w.image.image)
            self.assertIsNone(w.image.effective_bounds);self.assertFalse(w.image.isEnabled())
            self.assertIn('unavailable',w.readings.text());self.assertFalse(w.save_button.isEnabled())
            w.close();self.wait(lambda:not w.worker.isRunning())
    def test_changed_session_clears_old_roi_even_when_geometry_and_bounds_fit(self):
        from lmtr_test_support import segmented_plan
        from lmtr_reference.container import write_plan
        from offline_measurement import Rectangle
        with TemporaryDirectory() as temp:
            path=Path(temp)/'sessions.lmtr';write_plan(segmented_plan(same_geometry=True),path)
            w=self.window;w.open_path(path);self.wait(lambda:w.state is not None)
            w._request('seek',1);self.wait(lambda:not w._in_flight)
            w.image.set_roi(Rectangle(0,0,2,1));w.image.pointer=(1,0)
            w._request('seek',4);self.wait(lambda:not w._in_flight)
            self.assertIsNone(w.image.roi);self.assertIsNone(w.image.pointer)
            self.assertEqual((2,3),w.frame.geometry.shape)
            w.close();self.wait(lambda:not w.worker.isRunning())
    def test_failed_replacement_cannot_keep_previous_temperatures(self):
        self.opened();w=self.window
        w.open_path(ROOT/'committed-decoded-byte-corrupt.lmtr')
        self.wait(lambda:not w._in_flight)
        # Complete open may only validate navigation; selecting forces corrupt CHUNK.
        if w.state is not None:
            w._request('seek',1);self.wait(lambda:not w._in_flight)
        self.assertIsNone(w.frame);self.assertIsNone(w.state)
        self.assertIsNone(w.image.image);self.assertIn('Read failed',w.status.text())
    def test_auto_range_is_independent_of_invalid_manual_edits(self):
        self.opened();w=self.window
        w.minimum.setValue(50);w.maximum.setValue(40)
        w.palette.setCurrentText('Turbo')
        self.assertEqual('Turbo',w.image.palette)
        self.assertIsNotNone(w.image.effective_bounds)
        w.auto.setChecked(False)
        self.assertIn('Invalid locked range',w.range_status.text())
        w.maximum.setValue(60)
        self.assertEqual('',w.range_status.text())
        self.assertEqual(50,w.image.effective_bounds.lower)
    def test_png_matches_retained_presentation_during_invalid_locked_edits(self):
        self.opened();w=self.window
        w.minimum.setValue(50);w.maximum.setValue(40);w.auto.setChecked(False)
        w.palette.setCurrentText('White hot')
        self.assertNotEqual(w.palette.currentText(),w.image.palette)
        expected=w.frame.render(w.image.palette,w.image.effective_bounds)
        with TemporaryDirectory() as temp:
            target=Path(temp)/'actual-visible.png'
            with patch('lmtr_viewer.QFileDialog.getSaveFileName',return_value=(str(target),'')):w._save()
            actual=cv2.cvtColor(cv2.imread(str(target)),cv2.COLOR_BGR2RGB)
            np.testing.assert_array_equal(expected,actual)
