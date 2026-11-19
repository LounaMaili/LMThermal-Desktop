"""Periodic policy and actual clock evidence are presentation, never reconstructed measurements."""
import hashlib
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import time
import unittest
from PyQt6.QtWidgets import QApplication

from lmtr_reader import LmtrPlaybackModel,format_timing_evidence
from lmtr_viewer import LmtrWindow

SOURCE=Path(__file__).parent/'fixtures/lmtr/files/periodic-sampling.lmtr'


class TimingPresentationTests(unittest.TestCase):
    def value(self):
        return dict(kind='observation',timeline_ns='450000001',
            sampling=dict(mode='periodic',period_ns='200000000',origin_timeline_ns='0'),
            scheduled=dict(first_slot='2',last_slot='2'),receipt=None,receipt_shared_timeline_ns='440000123',
            measurement=dict(status='available',validity='all_valid'),reason='')

    def test_requested_deadline_does_not_replace_actual_intake_or_receipt(self):
        text=format_timing_evidence(self.value())
        self.assertIn('5 Hz periodic',text);self.assertIn('slot 2',text)
        self.assertIn('0.450000001 s',text);self.assertIn('0.440000123 s',text)
        self.assertNotIn('intake +0.400000000',text)

    def test_missed_schedule_range_has_a_count_and_detection_time_without_a_reading(self):
        value=self.value();value.update(kind='gap',measurement=None,category='missed_schedule',count='3',
            reason='no_new_observation_in_window',receipt_shared_timeline_ns=None,
            scheduled=dict(first_slot='4',last_slot='6'))
        text=format_timing_evidence(value)
        self.assertIn('slots 4–6',text);self.assertIn('missed_schedule',text);self.assertIn('count 3',text)
        self.assertNotIn('receipt',text)

    def test_unavailable_measurement_and_unaligned_clock_are_explicit(self):
        value=self.value();value.update(measurement=dict(status='unavailable',reason='session_not_ready'),
            receipt_shared_timeline_ns=None,receipt=dict(status='unreliable',stamp=dict(ticks='12345')))
        text=format_timing_evidence(value)
        self.assertIn('unavailable · session_not_ready',text);self.assertIn('12345 ticks',text)
        self.assertNotIn('receipt 0.',text)

    def test_noninteger_frequency_is_not_rounded_to_an_exact_fifteen_hz_claim(self):
        value=self.value();value['sampling']['period_ns']='66666667'
        text=format_timing_evidence(value)
        self.assertIn('1000000000/66666667 Hz',text);self.assertNotIn('15 Hz',text)

    def test_timing_projection_is_owned_and_keeps_the_original_recording_timeline(self):
        before=hashlib.sha256(SOURCE.read_bytes()).hexdigest();model=LmtrPlaybackModel(SOURCE)
        try:
            for i in range(model.entry_count):
                original=model.original_entry(i);timing=model.timing_evidence(i)
                self.assertEqual(timing['timeline_ns'],original['timeline_ns'])
                self.assertEqual(timing['scheduled'],original.get('scheduled'))
                if timing['sampling'] is not None:
                    expected=dict(timing['sampling']);timing['sampling']['period_ns']='7'
                    self.assertEqual(model.timing_evidence(i)['sampling'],expected)
        finally:model.close()
        self.assertEqual(before,hashlib.sha256(SOURCE.read_bytes()).hexdigest())

    def test_gap_description_preserves_category_and_unavailable_description_preserves_reason(self):
        gaps=unavailable=0
        # The unchanged periodic fixture has no gap; use the dedicated canonical loss/absence cases.
        for filename in ('illustration-05-stored.lmtr','illustration-06-stored.lmtr'):
            model=LmtrPlaybackModel(SOURCE.parent/filename)
            try:
                for i in range(model.entry_count):
                    original=model.original_entry(i);description=model.describe(i)
                    if original['kind']=='gap':
                        self.assertIn(original['category'],description.reason);gaps+=1
                    if original.get('measurement',{}).get('status')=='unavailable':
                        self.assertIn(original['measurement']['reason'],description.reason);unavailable+=1
            finally:model.close()
        self.assertGreater(gaps,0);self.assertGreater(unavailable,0)



class TimingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.window=LmtrWindow();self.window.show()
    def wait(self,condition):
        end=time.monotonic()+10
        while not condition() and time.monotonic()<end:self.app.processEvents();time.sleep(.003)
        self.assertTrue(condition(),self.window.status.text())
    def tearDown(self):
        self.window.close();self.wait(lambda:not self.window.worker.isRunning())
    def test_periodic_policy_and_actual_intake_are_visible_without_diagnostics_or_measurement_changes(self):
        w=self.window;w.open_path(SOURCE);self.wait(lambda:w.state is not None and not w._in_flight)
        self.assertIn('Requested:',w.timing_status.text());self.assertIn('Recorder intake',w.timing_status.text())
        self.assertIn('temperature planes',w.status.text());self.assertIn('absent measurements',w.status.text())
        self.assertFalse(w.details_panel.isVisible())
        height=w.timing_status.height();w.resize(850,650);self.app.processEvents()
        self.assertEqual(height,w.timing_status.height())
        if w.frame and w.frame.has_readings:
            original=w.frame.temperature_bytes;w.palette.setCurrentText('Turbo')
            self.assertEqual(original,w.frame.temperature_bytes)
    def test_new_or_failed_load_clears_the_preceding_timing_evidence_immediately(self):
        w=self.window;w.open_path(SOURCE);self.wait(lambda:w.state is not None and not w._in_flight)
        w.open_path(SOURCE.parent/'unknown-required-feature.lmtr')
        self.assertIn('unavailable',w.timing_status.text().lower())
        self.wait(lambda:'Read failed' in w.status.text())
        self.assertIn('unavailable',w.timing_status.text().lower());self.assertIsNone(w.frame)
