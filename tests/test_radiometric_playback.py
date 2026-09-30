"""Offline navigation, cache bounds, timing and matrix interpretation without Qt."""

import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from celsius_palette import CelsiusRange, render_temperature
from mvp_presentation import current_extrema, current_reading
from radiometric_playback import PlaybackModel, save_playback_png
from radiometric_recording import Recording, RecordingError, atomic_json, open_recording
from roi_measurement import roi_statistics
from playback_support import observations, build_bundle


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.frames=observations()

    def setUp(self):
        self.tmp=TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=build_bundle(self.root/'sequence.lmthermal',self.frames)
        self.model=PlaybackModel.open(self.path)

    def tearDown(self): self.tmp.cleanup()

    def test_entries_preserve_gap_drop_and_native_roi_without_eager_cache(self):
        self.assertTrue(self.model.completed)
        self.assertEqual(len(self.model.entries),9)
        self.assertEqual(self.model.entries[2].kind,'gap')
        self.assertEqual(self.model.entries[4].kind,'drop')
        self.assertEqual(self.model.entries[4].reason,'writer_overload')
        self.assertEqual(self.model.cache_size,0)
        self.assertEqual(self.model.chunk_loads,0)
        self.assertEqual(self.model.entries[0].roi.x1,160)
        self.assertIsNone(self.model.entries[-1].roi)

    def test_exact_stored_frames_cursor_extrema_and_separate_centers(self):
        for index in (0,3,8):
            frame=self.model.frame(index)
            source=self.frames[0 if index+1<9//2 else 1].measurement
            self.assertEqual(frame.raw14.dtype,np.dtype('uint16'))
            self.assertEqual(frame.temperature_c.dtype,np.dtype('float32'))
            self.assertEqual(frame.raw14.shape,(288,384))
            np.testing.assert_array_equal(frame.raw14,source.raw14)
            np.testing.assert_array_equal(frame.temperature_c,source.temperature_c)
            self.assertFalse(frame.temperature_c.flags.writeable)
            reading=current_reading(frame,(192,144))
            self.assertEqual(reading.raw14,source.literal_center_index)
            self.assertEqual(reading.native_equivalent_c,source.literal_center_c)
            self.assertEqual(frame.trailer_center_index,source.trailer_center_index)
            self.assertEqual(frame.trailer_center_c,source.trailer_center_c)
            self.assertEqual(current_extrema(frame),((source.high_xy,source.high_c),(source.low_xy,source.low_c)))
            self.assertIsNone(current_reading(frame,(384,288)))

    def test_gap_and_drop_have_no_matrix_and_navigation_counts_every_entry(self):
        self.model.seek(1)
        self.model.step(1)
        self.assertEqual(self.model.entry.sequence,3)
        self.assertIsNone(self.model.frame())
        self.model.step(1)
        self.assertEqual(self.model.entry.sequence,4)
        self.assertIsNotNone(self.model.frame())
        self.model.step(1)
        self.assertIsNone(self.model.frame())
        self.model.step(-1)
        self.assertEqual(self.model.entry.sequence,4)
        self.model.seek(100); self.assertEqual(self.model.index,8)
        self.model.seek(-1); self.assertEqual(self.model.index,0)

    def test_lazy_load_hits_and_bounded_cache_across_chunk_boundaries(self):
        with patch.object(Recording,'load_chunk',autospec=True,side_effect=Recording.load_chunk) as load:
            self.model.frame(0);self.model.frame(1)
            self.assertEqual(load.call_count,1)
            for index in (3,5,6,7,8):
                self.model.frame(index)
                self.assertLessEqual(self.model.cache_size,2)
            self.assertLessEqual(self.model.cache_bytes,2*2*288*384*6+32)
            calls=load.call_count
            self.model.frame(0)
            self.assertEqual(load.call_count,calls+1)
        with self.assertRaises(ValueError): PlaybackModel(self.model.recording,cache_chunks=3)

    def test_elapsed_timing_pause_speeds_and_no_presentation_catchup(self):
        self.model.play(now=10)
        self.model.advance(now=10.39)
        self.assertEqual(self.model.index,1)
        self.model.advance(now=10.41)
        self.assertEqual(self.model.index,2) # Gap is a timed position.
        self.model.pause(); self.model.advance(now=50)
        self.assertEqual(self.model.index,2)
        self.model.play(now=100)
        self.model.set_speed(2,now=100.1)
        self.model.advance(now=100.5)
        self.assertGreater(self.model.index,3)
        self.assertEqual(self.model.chunk_loads,0) # Clock/navigation never load arrays.
        self.model.advance(now=200)
        self.assertEqual(self.model.index,8)
        self.assertFalse(self.model.playing)
        self.model.play(now=300)
        self.assertEqual(self.model.index,0)
        with self.assertRaises(ValueError): self.model.set_speed(3)

    def test_rendering_and_inspection_roi_do_not_mutate_stored_values(self):
        frame=self.model.frame(3)
        before=(frame.payload.raw14_bytes,frame.payload.temperature_bytes,frame.metadata)
        stats=roi_statistics(frame.temperature_c,frame.roi)
        for palette in ('Inferno','White hot','Turbo'):
            for bounds in ((20,40),(25,45)):
                render_temperature(frame.temperature_c,*bounds,palette)
                self.assertEqual(roi_statistics(frame.temperature_c,frame.roi),stats)
        self.assertEqual((frame.payload.raw14_bytes,frame.payload.temperature_bytes,frame.metadata),before)
        region=frame.temperature_c[10:30,10:30]
        from roi_measurement import NativeROI
        inspect=roi_statistics(frame.temperature_c,NativeROI(10,10,30,30))
        self.assertEqual(inspect.mean_c,float(region.mean(dtype=np.float64)))

    def test_rendered_png_source_integrity_no_overwrite_or_inside_bundle(self):
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.rglob('*') if p.is_file()}
        frame=self.model.frame(3)
        path=self.root/'selected.png'
        save_playback_png(frame,path,'Inferno',CelsiusRange(20,40))
        self.assertTrue(path.exists())
        with self.assertRaises(FileExistsError): save_playback_png(frame,path,'Turbo',CelsiusRange(25,45))
        with self.assertRaises(ValueError): save_playback_png(frame,self.path/'render.png','Turbo',CelsiusRange(25,45))
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.rglob('*') if p.is_file()})

    def test_incomplete_uncommitted_reference_and_partial_tail_recovery(self):
        manifest=json.loads((self.path/'manifest.json').read_text())
        manifest['completed']=False
        manifest['chunks']=manifest['chunks'][:1]
        atomic_json(self.path/'manifest.json',manifest)
        with (self.path/'timeline.csv').open('a') as stream: stream.write('partial,tail')
        model=PlaybackModel.open(self.path)
        self.assertFalse(model.completed)
        self.assertIsNotNone(model.frame(0))
        self.assertEqual(model.entries[3].kind,'uncommitted')
        self.assertIsNone(model.frame(3))

    def test_corrupted_chunk_after_open_detected_on_cold_load(self):
        entry=self.model.recording.manifest['chunks'][0]
        path=self.path/'chunks'/entry['file']; path.write_bytes(b'corrupt')
        with self.assertRaises(RecordingError): self.model.frame(0)
        with self.assertRaises(RecordingError): PlaybackModel.open(self.path)
        path.unlink()
        with self.assertRaises(RecordingError): PlaybackModel.open(self.path)

    def test_loader_rejects_backward_elapsed_time_and_unlabelled_unstored_valid(self):
        path=self.path/'timeline.csv'
        with path.open(newline='') as stream:
            reader=csv.DictReader(stream); fields=reader.fieldnames; rows=list(reader)
        rows[2]['elapsed_s']='0.01'
        with path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        with self.assertRaises(RecordingError): open_recording(self.path)
    def test_unstored_valid_sample_requires_an_explicit_overload_drop(self):
        path=self.path/'timeline.csv'
        with path.open(newline='') as stream:
            reader=csv.DictReader(stream); fields=reader.fieldnames; rows=list(reader)
        rows[4]['dropped']='false'
        with path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        with self.assertRaisesRegex(RecordingError,'explicit overload'):
            open_recording(self.path)

    def test_empty_recording_has_no_frame_or_playable_timeline(self):
        from radiometric_recorder import RadiometricRecorder
        from playback_support import manual_sampler
        path=self.root/'empty.lmthermal'
        with patch.object(RadiometricRecorder,'_sample_loop',manual_sampler):
            recorder=RadiometricRecorder(path)
            recorder.stop()
        model=PlaybackModel.open(path)
        self.assertIsNone(model.entry)
        self.assertIsNone(model.frame())
        model.play(now=0)
        self.assertFalse(model.playing)
        self.assertIsNone(model.seek(5))



if __name__=='__main__': unittest.main()
