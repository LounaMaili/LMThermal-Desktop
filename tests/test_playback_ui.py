"""Asynchronous Qt playback state checks with fixture-derived recordings."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication

from celsius_palette import CelsiusRange
from lmthermal_viewer import MainWindow
from camera_worker_support import CameraWorkerStub as CameraWorker
from measurement_logger import MeasurementLogger
from radiometric_recorder import RadiometricRecorder
from radiometric_export import snapshot_capture, export_capture
from playback_worker import PlaybackWorker
from roi_measurement import NativeROI, roi_statistics
from playback_support import observations,build_bundle


class PlaybackUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.frames=observations()

    def setUp(self):
        self.worker_patch = patch('lmthermal_viewer.CameraWorker', CameraWorker)
        self.platform_patch = patch('lmthermal_viewer.sys.platform', 'linux')
        self.worker_patch.start()
        self.platform_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.addCleanup(self.platform_patch.stop)
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=build_bundle(self.root/'test.lmthermal',self.frames)
        self.window=MainWindow(auto_connect=False)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.tmp.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate(),self.window.playback_status_label.text())

    def opened(self):
        with patch.object(CameraWorker,'start') as camera_start:
            self.assertTrue(self.window.open_recording(self.path))
            self.wait(lambda:self.window.playback is not None and self.window.observation is not None)
            camera_start.assert_not_called()
        return self.window.playback

    def select(self,index,kind='valid'):
        self.window.timeline_slider.setValue(index)
        self.wait(lambda:self.window.playback.index==index and
                  f'sequence {index+1} |' in self.window.playback_status_label.text())
        self.assertEqual(self.window.playback.entry.kind,kind)

    def test_open_without_camera_and_slider_exposes_all_entries(self):
        self.opened()
        self.assertIsNone(self.window.worker)
        self.assertIsNone(self.window.offline_capture)
        self.assertEqual(self.window.timeline_slider.maximum(),8)
        self.assertFalse(self.window.initialize_button.isEnabled())
        self.assertFalse(self.window.capture_button.isEnabled())
        self.assertFalse(self.window.start_log_button.isEnabled())
        self.assertFalse(self.window.start_record_button.isEnabled())
        self.select(2,'gap')
        self.assertIsNone(self.window.observation)
        self.assertIsNone(self.window.image_widget.image)
        self.assertIsNone(self.window.legend.bounds)
        self.assertEqual(self.window.high_label.text(),'Unavailable')
        self.assertIn('gap',self.window.state_label.text())
        self.assertFalse(self.window.render_button.isEnabled())
        self.select(4,'drop')
        self.assertIn('Recorder drop',self.window.state_label.text())
        self.assertIn('writer_overload',self.window.playback_status_label.text())
        self.window._on_hover((192,144))
        self.assertEqual(self.window.cursor_label.text(),'Unavailable')
        self.select(5)
        self.assertTrue(self.window.render_button.isEnabled())

    def test_roi_restore_inspection_clear_and_presentation_values(self):
        self.opened()
        self.assertEqual(self.window.image_widget.roi,NativeROI(160,180,220,240))
        self.assertEqual(self.window.playback_roi_label.text(),'Recorded ROI')
        self.window.image_widget.set_roi(NativeROI(10,10,30,30))
        self.assertEqual(self.window.playback_roi_label.text(),'Inspection ROI')
        reading=self.window.observation
        values=(self.window.high_label.text(),self.window.low_label.text(),self.window.center_pixel_label.text(),
                self.window.trailer_center_label.text(),self.window.roi_values_label.text())
        self.window._on_hover((192,144))
        self.assertIn(str(reading.literal_center_index),self.window.cursor_label.text())
        self.window.palette_combo.setCurrentText('Turbo')
        self.window.auto_range_check.setChecked(False)
        self.window.min_spin.setValue(25);self.window.max_spin.setValue(45)
        self.window.resize(1200,800)
        self.assertEqual(values,(self.window.high_label.text(),self.window.low_label.text(),self.window.center_pixel_label.text(),
                                self.window.trailer_center_label.text(),self.window.roi_values_label.text()))
        self.window.image_widget.clear_roi()
        self.assertEqual(self.window.playback_roi_label.text(),'Inspection ROI: none')
        self.select(1)
        self.assertEqual(self.window.image_widget.roi,NativeROI(160,180,220,240))
        self.assertEqual(self.window.playback_roi_label.text(),'Recorded ROI')

    def test_play_pause_and_step_across_chunks_without_camera(self):
        model=self.opened()
        self.window.play_button.click()
        self.assertTrue(model.playing)
        self.wait(lambda:model.index>=2)
        self.window.play_button.click()
        index=model.index
        self.assertFalse(model.playing)
        time.sleep(.05);self.app.processEvents()
        self.assertEqual(model.index,index)
        self.window.next_sample_button.click()
        self.wait(lambda:model.index==index+1 and f'sequence {index+2} |' in self.window.playback_status_label.text())
        self.window.previous_sample_button.click()
        self.wait(lambda:model.index==index and f'sequence {index+1} |' in self.window.playback_status_label.text())
        self.window.last_sample_button.click()
        self.wait(lambda:model.index==8 and 'sequence 9 |' in self.window.playback_status_label.text())
        self.window.first_sample_button.click()
        self.wait(lambda:model.index==0 and 'sequence 1 |' in self.window.playback_status_label.text())

    def test_rendered_png_and_source_hashes_remain_unchanged(self):
        self.opened()
        hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.rglob('*') if p.is_file()}
        selected=self.root/'selected.png'
        self.assertEqual(self.window.render_button.text(),'Save selected frame PNG…')
        with patch('lmthermal_viewer.QFileDialog.getSaveFileName',return_value=(str(selected),'')) as dialog:
            self.window._save_rendered()
            suggested=Path(dialog.call_args.args[2])
            self.assertEqual(suggested.parent,self.path.parent)
            self.assertTrue(suggested.name.endswith('sample-000001.png'))
        self.assertTrue(selected.exists())
        self.assertEqual(hashes,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.rglob('*') if p.is_file()})

    def test_live_still_sequence_isolation_and_finalization(self):
        with patch.object(CameraWorker,'start'):
            self.window.connect_camera()
        worker=self.window.worker
        self.window.logger=MeasurementLogger(self.root/'log.csv')
        logger=self.window.logger
        self.window.open_recording(self.path)
        self.wait(lambda:self.window.playback is not None)
        self.assertFalse(worker.isRunning())
        self.assertIsNone(self.window.logger)
        self.assertFalse(logger._thread.is_alive())
        self.window._on_failure('queued old failure')
        self.window._on_connected('queued old device')
        self.assertIn('Offline',self.window.device_label.text())
        capture=export_capture(snapshot_capture(self.frames[1],'Inferno',CelsiusRange(20,40),True),self.root/'still')
        self.assertTrue(self.window.open_capture(capture['.json']))
        self.assertIsNone(self.window.playback)
        self.assertIsNone(self.window.playback_worker)
        self.assertIsNotNone(self.window.offline_capture)
        self.window.open_recording(self.path)
        self.wait(lambda:self.window.playback is not None)
        self.assertIsNone(self.window.offline_capture)
        with patch.object(CameraWorker,'start'):
            self.window.connect_camera()
        self.assertIsNone(self.window.playback)
        self.assertIsNone(self.window.observation)
        self.assertFalse(self.window.playback_group.isVisible())
        self.window._on_playback_result() # Late/absent sender cannot revive offline data.
        self.window.stop_camera()

    def test_incomplete_status_and_corruption_error_clear_all_measurements(self):
        path=self.path/'manifest.json';manifest=json.loads(path.read_text());manifest['completed']=False
        path.write_text(json.dumps(manifest))
        self.opened()
        self.assertIn('INCOMPLETE',self.window.state_label.text())
        self.window.close_recording()
        (self.path/'chunks'/manifest['chunks'][0]['file']).unlink()
        self.window.open_recording(self.path)
        self.wait(lambda:'integrity error' in self.window.playback_status_label.text())
        self.assertIsNone(self.window.observation)
        self.assertEqual(self.window.high_label.text(),'Unavailable')

    def test_worker_pending_requests_and_results_are_coalesced(self):
        from radiometric_playback import PlaybackModel
        model=PlaybackModel.open(self.path)
        worker=PlaybackWorker()
        worker.request(1,model=model,index=0)
        worker.request(2,model=model,index=1)
        worker.request(3,model=model,index=3)
        self.assertEqual(worker._pending[0],3)
        worker.start()
        deadline=time.monotonic()+3
        while worker._result is None and time.monotonic()<deadline: time.sleep(.002)
        result=worker.take_latest()
        self.assertEqual(result[0],3)
        self.assertEqual(result[2],3)
        self.assertIsNone(worker.take_latest())
        worker.request_stop(); self.assertTrue(worker.wait(3000))


if __name__=='__main__': unittest.main()
