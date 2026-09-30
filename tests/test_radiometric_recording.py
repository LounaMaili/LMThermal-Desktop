"""Exact recording persistence, gaps, bounds and interruption without hardware."""

import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from celsius_palette import render_temperature
from measurement_logger import create_sample
from radiometric_recorder import RadiometricRecorder
from radiometric_recording import (ChunkWriter, RecordingError, atomic_json, open_recording,
                                   snapshot_frame, HANDOFF_FRAMES)
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import NativeROI

HAND = (Path(__file__).parent / 'fixtures/warm-hand-settled.raw').read_bytes()


def manual_sampler(recorder):
    """Keep the production writer/lifecycle while driving sample instants explicitly."""
    recorder._stop.wait()
    recorder._timeline.flush()
    recorder._timeline.close()
    recorder._sampler_done.set()


class RecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.measurement = make_measurement(HAND, 0)
        cls.ready = FrameObservation(HAND, 0, SessionState.RADIOMETRIC_READY,
                                     inspect_frame(HAND), False, None, cls.measurement)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(RadiometricRecorder, '_sample_loop', manual_sampler)
        self.patch.start()
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            try:
                recorder.stop()
            except OSError:
                pass
        self.patch.stop()
        self.tmp.cleanup()

    def recorder(self, **kwargs):
        recorder = RadiometricRecorder(self.root/'test.lmthermal', **kwargs)
        self.recorders.append(recorder)
        return recorder

    def tick(self, recorder, sequence, observation=None, roi=None, **kwargs):
        recorder.update_latest(replace(self.ready) if observation is None else observation, roi)
        return recorder.record_instant(sequence, recorder.started+sequence/recorder.rate_hz, **kwargs)

    def completed(self, count=3, chunk_frames=2):
        recorder = self.recorder(chunk_frames=chunk_frames)
        for sequence in range(1,count+1):
            self.tick(recorder, sequence)
            recorder.handoff.join()
        recorder.stop()
        return open_recording(recorder.directory)

    def test_snapshot_exact_immutable_and_no_source_mutation_or_transport(self):
        row = create_sample(self.ready, None, timestamp_utc='2026-09-30T12:00:00+00:00', elapsed_s=1, sequence=1)
        before = self.measurement.temperature_c.tobytes()
        frame = snapshot_frame(self.ready,row)
        self.assertEqual(frame.raw14.dtype, np.dtype('uint16'))
        self.assertEqual(frame.temperature_c.dtype, np.dtype('float32'))
        self.assertEqual(frame.raw14.shape,(288,384))
        np.testing.assert_array_equal(frame.raw14,self.measurement.raw14)
        self.assertEqual(frame.temperature_bytes,before)
        self.assertFalse(frame.raw14.flags.writeable)
        self.assertFalse(frame.temperature_c.flags.writeable)
        self.assertFalse(frame.metadata['transport_preserved'])
        self.assertEqual(frame.metadata['parameters']['emissivity'], self.measurement.parameters.emissivity)
        for palette in ('White hot','Inferno','Turbo'):
            render_temperature(self.measurement.temperature_c, 20,40,palette)
        self.assertEqual(snapshot_frame(self.ready,row),frame)
        self.assertEqual(self.measurement.temperature_c.tobytes(),before)

    def test_snapshot_rejects_transient_even_with_forged_valid_flag(self):
        row=create_sample(self.ready,None,timestamp_utc='now',elapsed_s=1,sequence=1)
        with self.assertRaises(ValueError):
            snapshot_frame(replace(self.ready,state=SessionState.SHUTTER_TRANSIENT),row)

    def test_roundtrip_rollover_mapping_and_final_counts(self):
        recording=self.completed(5)
        self.assertTrue(recording.manifest['completed'])
        self.assertEqual([c['frames'] for c in recording.manifest['chunks']],[2,2,1])
        self.assertEqual(recording.manifest['requested_samples'],5)
        self.assertEqual(recording.manifest['valid_frames'],5)
        self.assertEqual(recording.manifest['uncommitted_frames'],0)
        for sequence in range(1,6):
            frame=recording.frame(sequence)
            self.assertEqual(frame.sequence,sequence)
            np.testing.assert_array_equal(frame.temperature_c,self.measurement.temperature_c)
            np.testing.assert_array_equal(frame.raw14,self.measurement.raw14)
        with self.assertRaises(KeyError): recording.frame(6)
        self.assertFalse(list((recording.directory/'chunks').glob('*.incomplete')))

    def test_mixed_gaps_held_and_roi_move_clear(self):
        recorder=self.recorder(chunk_frames=2)
        roi=NativeROI(160,180,220,240)
        self.tick(recorder,1,roi=roi)
        self.tick(recorder,2,replace(self.ready,state=SessionState.SHUTTER_TRANSIENT),roi=roi)
        self.tick(recorder,3,roi=NativeROI(10,10,30,30))
        recorder.handoff.join()
        self.tick(recorder,4)
        # No new observation must not repeat the last valid matrix.
        row=recorder.record_instant(5,recorder.started+1)
        self.assertEqual(row['status'],'no_new_observation')
        recorder.stop()
        recording=open_recording(recorder.directory)
        self.assertEqual(recording.manifest['gap_samples'],2)
        self.assertIsNone(recording.frame(2)); self.assertIsNone(recording.frame(5))
        self.assertEqual(recording.frame(1).metadata['summary']['roi_x1_px'],160)
        self.assertEqual(recording.frame(3).metadata['summary']['roi_x1_px'],10)
        self.assertIsNone(recording.frame(4).metadata['summary']['roi_mean_c'])
        self.assertEqual(recording.timeline[1]['high_c'],'')

    def test_per_frame_parameters_not_assumed_constant(self):
        recorder=self.recorder()
        self.tick(recorder,1)
        altered=replace(self.measurement,parameters=replace(self.measurement.parameters,emissivity=1.0))
        self.tick(recorder,2,replace(self.ready,measurement=altered))
        recorder.stop()
        recording=open_recording(recorder.directory)
        self.assertNotEqual(recording.frame(1).metadata['parameters']['emissivity'],
                            recording.frame(2).metadata['parameters']['emissivity'])

    def test_bounded_writer_overload_drops_without_blocking(self):
        entered,release=threading.Event(),threading.Event()
        original=ChunkWriter.commit
        def blocked(writer):
            if writer.pending:
                entered.set(); release.wait(5)
            return original(writer)
        with patch.object(ChunkWriter,'commit',blocked):
            recorder=self.recorder(chunk_frames=1)
            try:
                self.tick(recorder,1)
                self.assertTrue(entered.wait(2))
                for seq in range(2,10): self.tick(recorder,seq)
                self.assertEqual(recorder.handoff.maxsize,HANDOFF_FRAMES)
                self.assertEqual(recorder.handoff.qsize(),4)
                self.assertEqual(recorder.status.dropped,4)
                release.set(); recorder.stop()
            finally: release.set()
        recording=open_recording(recorder.directory)
        self.assertEqual(recording.manifest['valid_frames'],5)
        self.assertEqual(recording.manifest['dropped_samples'],4)
        self.assertEqual(recording.timeline[-1]['status'],'writer_overload')
        self.assertEqual(recording.timeline[-1]['measurement_valid'],'true')
        self.assertIsNone(recording.frame(9))

    def test_late_sampler_records_empty_missed_instants_not_catchup_matrices(self):
        recorder=self.recorder()
        self.tick(recorder,1,missed=True)
        self.tick(recorder,2,missed=True)
        self.tick(recorder,3)
        recorder.stop()
        recording=open_recording(recorder.directory)
        self.assertEqual(recording.manifest['dropped_samples'],2)
        self.assertEqual(recording.timeline[0]['status'],'sampler_missed_deadline')
        self.assertEqual(recording.timeline[0]['high_c'],'')
        self.assertIsNone(recording.frame(1))
        self.assertIsNotNone(recording.frame(3))

    def test_interruption_keeps_committed_chunks_ignores_partial_and_orphans(self):
        recorder=self.recorder(chunk_frames=2)
        for seq in (1,2): self.tick(recorder,seq)
        recorder.handoff.join()
        self.tick(recorder,3)
        recorder.handoff.join()
        partial=recorder.directory/'chunks/.chunk-abandoned.incomplete'
        partial.write_bytes(b'partial npz')
        # Before stop: first two are committed, third only has a timeline reference.
        recording=open_recording(recorder.directory)
        self.assertFalse(recording.manifest['completed'])
        self.assertIsNotNone(recording.frame(1))
        self.assertIsNone(recording.frame(3))
        self.assertTrue(partial.exists())
        recorder.stop()
        self.assertIsNotNone(open_recording(recorder.directory).frame(3))

    def test_existing_destination_and_invalid_rate_protected(self):
        recorder=self.recorder()
        with self.assertRaises(FileExistsError): RadiometricRecorder(recorder.directory)
        with self.assertRaises(ValueError): RadiometricRecorder(self.root/'bad',rate_hz=30)
        self.assertFalse((self.root/'bad').exists())

    def test_corrupt_or_missing_chunk_rejected(self):
        recording=self.completed()
        path=recording.directory/'chunks'/recording.manifest['chunks'][0]['file']
        original=path.read_bytes(); path.write_bytes(original[:-1]+b'x')
        with self.assertRaises(RecordingError): open_recording(recording.directory)
        path.unlink()
        with self.assertRaises(RecordingError): open_recording(recording.directory)

    def test_unsupported_version_counts_and_nonfinite_json_rejected(self):
        recording=self.completed()
        path=recording.directory/'manifest.json'
        original=path.read_text()
        for key,value in (('version',2),('requested_samples',99),('duration_s',float('nan')),('duration_s',float('inf'))):
            manifest=json.loads(original); manifest[key]=value
            path.write_text(json.dumps(manifest))
            with self.assertRaises(RecordingError): open_recording(recording.directory)
        path.write_text(original.replace('"duration_s":', '"invalid_numeric": 1e999, "duration_s":'))
        with self.assertRaises(RecordingError): open_recording(recording.directory)
        path.write_text(original)
        with self.assertRaises(ValueError): atomic_json(path, {'value':float('nan')})
        self.assertEqual(path.read_text(),original)

    def test_wrong_chunk_dtype_shape_or_metadata_rejected_even_with_updated_hash(self):
        recording=self.completed()
        entry=recording.manifest['chunks'][0]; path=recording.directory/'chunks'/entry['file']
        with np.load(path,allow_pickle=False) as data: original={k:data[k].copy() for k in data.files}
        for changed in ({'raw14':original['raw14'].astype(np.uint32)},
                        {'temperature_c':original['temperature_c'][:,:287,:]},
                        {'temperature_c':np.full_like(original['temperature_c'],np.nan)},
                        {'metadata_utf8':np.frombuffer(b'[]',dtype=np.uint8)}):
            np.savez_compressed(path,**(original|changed))
            content=path.read_bytes(); entry.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
            atomic_json(recording.directory/'manifest.json',recording.manifest)
            with self.assertRaises(RecordingError): open_recording(recording.directory)

    def test_writer_failure_leaves_incomplete_readable_manifest(self):
        recorder=self.recorder(chunk_frames=1)
        with patch.object(ChunkWriter,'commit',side_effect=OSError('disk full')):
            self.tick(recorder,1)
            with self.assertRaises(OSError): recorder.stop()
        recording=open_recording(recorder.directory)
        self.assertFalse(recording.manifest['completed'])
        self.assertEqual(recording.manifest['error'],'disk full')
        self.assertIsNone(recording.frame(1))

    def test_timeline_tampering_cannot_disagree_with_saved_matrix(self):
        recording=self.completed()
        path=recording.directory/'timeline.csv'
        with path.open(newline='') as stream:
            reader=csv.DictReader(stream); columns=reader.fieldnames; rows=list(reader)
        rows[0]['high_c']='99.0'
        with path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=columns)
            writer.writeheader(); writer.writerows(rows)
        with self.assertRaises(RecordingError): open_recording(recording.directory)

    def test_real_monotonic_sampler_never_repeats_a_single_observation(self):
        import time
        self.patch.stop()
        try:
            recorder=self.recorder(rate_hz=25,observation=self.ready)
            deadline=time.monotonic()+2
            while recorder.status.requested_samples<3 and time.monotonic()<deadline:
                threading.Event().wait(.01)
            recorder.stop()
            recording=open_recording(recorder.directory)
            self.assertGreaterEqual(recording.manifest['requested_samples'],3)
            self.assertEqual(recording.manifest['valid_frames'],1)
            self.assertTrue(all(row['status']=='no_new_observation' for row in recording.timeline[1:]))
            self.assertEqual(float(recording.timeline[2]['scheduled_elapsed_s']),.12)
        finally:
            self.patch.start()

    def test_chunk_bound_and_exclusive_atomic_publication(self):
        directory=self.root/'chunks'; directory.mkdir()
        row=create_sample(self.ready,None,timestamp_utc='now',elapsed_s=1,sequence=1)
        payload=snapshot_frame(self.ready,row)
        for size in (0,17):
            with self.assertRaises(ValueError): ChunkWriter(directory,chunk_frames=size)
        writer=ChunkWriter(directory,chunk_frames=1)
        target=directory/'chunk-000001.npz'; target.write_bytes(b'keep')
        with self.assertRaises(FileExistsError): writer.append(payload)
        self.assertEqual(target.read_bytes(),b'keep')


if __name__ == '__main__': unittest.main()
