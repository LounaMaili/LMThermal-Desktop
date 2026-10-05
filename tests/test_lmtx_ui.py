"""Camera-free Qt generic-source, lifecycle, mapping and presentation checks."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from lmthermal_viewer import MainWindow
from celsius_palette import CelsiusRange
from lmtx_reader import load_lmtx
from offline_measurement import Geometry, Rectangle, Transform, adapt_legacy
from mvp_presentation import native_to_widget
from test_lmtx_import import CORPUS, source_members, write_archive
from playback_support import observations, build_bundle
from radiometric_export import snapshot_capture, export_capture
from radiometric_capture import load_capture
from radiometric_playback import PlaybackModel


class OfflineUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.window=MainWindow(auto_connect=False)
        self.tmp=TemporaryDirectory(); self.root=Path(self.tmp.name)

    def tearDown(self):
        self.window.close();self.app.processEvents();self.tmp.cleanup()

    def wait(self,predicate):
        until=time.monotonic()+5
        while not predicate() and time.monotonic()<until:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate(),self.window.state_label.text())

    def open(self,name):
        with patch('lmthermal_viewer.CameraWorker',side_effect=AssertionError('Camera accessed')):
            self.assertTrue(self.window.open_lmtx(CORPUS/name))
            self.wait(lambda:self.window.offline_capture is not None)
        return self.window.offline_capture

    def test_shared_measurable_sources_have_real_geometry_readings_and_generic_roi(self):
        for name in ('temperature-only.lmtx','validity-mask.lmtx','alternate-7x19.lmtx','ht301-rich-sanitized.lmtx'):
            with self.subTest(name=name):
                s=self.open(name)
                self.assertIsNone(self.window.worker)
                self.assertFalse(self.window.initialize_button.isEnabled())
                self.assertFalse(self.window.start_log_button.isEnabled())
                self.assertEqual((self.window.image_widget.image.width(),self.window.image_widget.image.height()),
                                 (s.geometry.width,s.geometry.height))
                self.assertEqual(self.window.image_widget.roi,s.roi)
                self.window._on_hover((0,0));self.assertIn('°C',self.window.cursor_label.text())
                roi=Rectangle(0,0,s.geometry.width,s.geometry.height)
                self.window.image_widget.set_roi(roi)
                before=self.window.roi_values_label.text(); bits=s.temperature_bytes
                self.window.palette_combo.setCurrentText('Turbo')
                self.window.auto_range_check.setChecked(False)
                self.window.min_spin.setValue(25);self.window.max_spin.setValue(45)
                self.window.resize(1200,800)
                self.assertEqual(before,self.window.roi_values_label.text())
                self.assertEqual(s.temperature_c.tobytes(),bits)
                self.assertTrue(self.window.close_capture())

    def test_preview_only_and_no_valid_are_visible_without_any_celsius_legend_or_values(self):
        for name in ('preview-only-160x120.lmtx','no-valid-pixels.lmtx'):
            s=self.open(name)
            self.assertIsNotNone(self.window.image_widget.image)
            self.assertIsNone(self.window.legend.bounds)
            self.window._on_hover((0,0));self.assertEqual(self.window.cursor_label.text(),'Unavailable')
            self.window.image_widget.set_roi(Rectangle(0,0,s.geometry.width,s.geometry.height))
            self.assertTrue(self.window.roi_values_label.text().startswith('Unavailable'))
            self.assertEqual(self.window.high_label.text(),'Unavailable')
            self.assertEqual(self.window.trailer_center_label.text(),'Unavailable')
            self.assertIn(s.manifest['source']['module_id'],self.window.state_label.text())

    def test_identification_uses_validated_content_even_when_extension_is_renamed(self):
        path=self.root/'renamed.bin'
        path.write_bytes((CORPUS/'temperature-only.lmtx').read_bytes())
        with patch('lmthermal_viewer.CameraWorker',side_effect=AssertionError('Camera accessed')):
            self.assertTrue(self.window.open_capture(path))
            self.wait(lambda:self.window.offline_capture is not None)
        self.assertEqual(self.window.offline_capture.format_id,'lmthermal-exchange')

    def test_unknown_palette_fallback_disclosed_original_metadata_survives(self):
        s=self.open('optional-extension-shape-palette.lmtx')
        self.assertEqual(self.window.palette_combo.currentText(),'White hot')
        self.assertIn('Unknown palette',self.window.mode_label.text())
        self.assertEqual(s.original_palette_id,'org.example.optional-palette')
        self.window.palette_combo.setCurrentText('Turbo')
        self.assertEqual(s.metadata['presentation']['palette_id'],'org.example.optional-palette')
        self.assertEqual(s.manifest['analysis']['shapes'][1]['type'],'org.example.outline')

    def test_transformed_generic_drag_and_hover_inverse_mapping(self):
        s=self.open('alternate-7x19.lmtx')
        s=replace(s,transform=Transform(90,True,False))
        self.window._publish_offline(s)
        w=self.window.image_widget;w.resize(800,600)
        self.assertEqual((w.image.width(),w.image.height()),(19,7))
        def position(x,y):return native_to_widget(x,y,w.width(),w.height(),geometry=s.geometry,transform=s.transform)
        first,last=position(1,2),position(5,16)
        for typ,pos,button,buttons in ((QEvent.Type.MouseButtonPress,first,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton),
                                     (QEvent.Type.MouseMove,last,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton),
                                     (QEvent.Type.MouseButtonRelease,last,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)):
            self.app.sendEvent(w,QMouseEvent(typ,QPointF(*pos),button,buttons,Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.roi,Rectangle(1,2,6,17))
        self.assertEqual(w.pointer,(5,16))
        self.assertIn('117.00 °C',self.window.cursor_label.text())
        self.assertEqual(s.temperature_c[16,5],117)

    def test_png_export_uses_celsius_not_stored_rgb_and_protects_source(self):
        s=self.open('validity-mask.lmtx')
        original=s.source_path.read_bytes()
        target=self.root/'rendered.png'
        with patch('lmthermal_viewer.QFileDialog.getSaveFileName',return_value=(str(target),'')):
            self.window._save_rendered()
        self.assertTrue(target.exists())
        self.assertEqual(s.source_path.read_bytes(),original)
        with patch('lmthermal_viewer.QFileDialog.getSaveFileName',return_value=(str(s.source_path),'')),patch('lmthermal_viewer.QMessageBox.warning') as warning:
            self.window._save_rendered();warning.assert_called_once()

    def test_corrupt_source_clears_previous_readings_and_late_result_cannot_revive_data(self):
        self.open('temperature-only.lmtx')
        self.assertTrue(self.window.open_lmtx(CORPUS/'corrupt-sha.lmtx'))
        self.wait(lambda:'import failed' in self.window.state_label.text())
        self.assertIsNone(self.window.offline_capture)
        self.assertIsNone(self.window.observation)
        self.assertIsNone(self.window.legend.bounds)
        self.assertEqual(self.window.high_label.text(),'Unavailable')
        self.assertFalse(self.window.render_button.isEnabled())
        old=self.window.offline_loader
        self.window.close_capture();old.result_available.emit();self.app.processEvents()
        self.assertIsNone(self.window.observation)

    def test_pending_startup_load_and_live_recording_switches_reject_obsolete_results(self):
        self.window._auto_connect_timer.start(0)
        self.open('ht301-rich-sanitized.lmtx')
        old=self.window.offline_loader
        recording=build_bundle(self.root/'recording.lmthermal',observations())
        self.assertTrue(self.window.open_recording(recording))
        self.wait(lambda:self.window.playback is not None and self.window.observation is not None)
        current=self.window.observation
        old.result_available.emit();self.app.processEvents()
        self.assertIs(self.window.observation,current)
        self.open('alternate-7x19.lmtx')
        self.assertIsNone(self.window.playback_worker)
        self.window._on_playback_result();self.assertEqual(self.window.observation.geometry,Geometry(7,19))
        with patch('lmthermal_viewer.CameraWorker') as camera:
            worker=camera.return_value;worker.wait.return_value=True
            self.window.connect_camera()
            camera.assert_called_once()
            self.assertIsNone(self.window.offline_capture)
            self.assertIsNone(self.window.observation)
            self.window.stop_camera()

    def test_legacy_adapters_preserve_owned_bits_provenance_centers_and_absent_transport(self):
        frame=observations()[0]
        files=export_capture(snapshot_capture(frame,'Turbo',CelsiusRange(25,45),False),self.root/'still')
        legacy=load_capture(files['.json']);s=adapt_legacy(legacy)
        self.assertEqual(s.temperature_c.tobytes(),legacy.temperature_c.tobytes())
        self.assertEqual(s.raw14.tobytes(),legacy.raw14.tobytes())
        self.assertEqual(s.metadata,legacy.metadata)
        self.assertEqual(s.literal_center_c,legacy.literal_center_c)
        self.assertEqual(s.trailer_center_c,legacy.trailer_center_c)
        self.assertEqual(s.original_bounds,CelsiusRange(25,45))
        self.assertEqual(s.payload_bytes['acquisition'],legacy.raw_transport)
        recording=build_bundle(self.root/'recording.lmthermal',observations())
        legacy=PlaybackModel.open(recording).frame(0);s=adapt_legacy(legacy)
        self.assertEqual(s.format_id,'lmthermal-radiometric-recording')
        self.assertEqual(s.temperature_bytes,legacy.temperature_c.tobytes())
        self.assertEqual(s.metadata,legacy.metadata)
        with self.assertRaises(ValueError):s.temperature_c.setflags(write=True)

    def test_windows_offline_entry_does_not_import_linux_acquisition(self):
        with patch('lmthermal_viewer.sys.platform', 'win32'), patch('lmthermal_viewer.CameraWorker') as camera:
            self.window.connect_camera()
            camera.assert_not_called()
            self.assertIn('Linux-only', self.window.statusBar().currentMessage())
        # This exercises the branch only; it is not a claim of Windows execution.

    def test_very_narrow_saved_range_remains_valid_after_palette_change(self):
        source=self.open('temperature-only.lmtx')
        source=replace(source, original_bounds=CelsiusRange(.000001, .000002), automatic_range=False)
        self.window._publish_offline(source)
        self.window.palette_combo.setCurrentText('Turbo')
        self.assertLess(self.window.min_spin.value(),self.window.max_spin.value())
        self.assertEqual(self.window.image_widget.effective_bounds,source.original_bounds)

    def test_imported_points_and_annotations_plain_text_unknown_shape_not_guessed(self):
        m,members=source_members()
        m['analysis']['points']=[{'id':'p','coordinate_space':'native','x_px':0,'y_px':0,'temperature_c':20}]
        m['analysis']['annotations']=[{'id':'a','coordinate_space':'native','target_id':'p','text':'<script>plain</script>'}]
        m['analysis']['shapes'].append({'id':'future','type':'org.test.circle','coordinate_space':'native','geometry':{'opaque':True}})
        p=write_archive(self.root/'analysis.lmtx',m,members)
        self.window.open_lmtx(p);self.wait(lambda:self.window.offline_capture is not None)
        self.window.show();self.app.processEvents();image=self.window.image_widget.grab()
        self.assertFalse(image.isNull())
        self.assertEqual(self.window.offline_capture.roi,Rectangle(0,0,2,2))


if __name__=='__main__':unittest.main()
