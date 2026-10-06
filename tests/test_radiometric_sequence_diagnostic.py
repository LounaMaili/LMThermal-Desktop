"""Protocol, byte boundaries and failure behavior without an attached camera."""

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

if not sys.platform.startswith('linux'):
    raise unittest.SkipTest('Linux V4L2 sequence diagnostic; offline readers do not import it')

from measurement_baseline import IMAGE_BYTES
from radiometric_mode_diagnostic import frame_metrics
from radiometric_sequence_diagnostic import Experiment, parameter_commands

FIXTURE = (Path(__file__).parent / 'fixtures' / 'scene-a.raw').read_bytes()


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Stream:
    def __init__(self, clock):
        self.clock = clock
        self.reads = 0

    def check(self):
        pass

    def drain(self):
        return []

    def next(self):
        self.reads += 1
        self.clock.sleep(.04)
        return self.clock.now, (Path(__file__).parent / 'fixtures' / 'radiometric-initial.raw').read_bytes()


class Control:
    def __init__(self, fail=None):
        self.values = []
        self.fail = fail

    def get(self):
        return self.values[-1] if self.values else 0

    def set(self, value):
        if value == self.fail:
            raise OSError('control failure')
        self.values.append(value)


class SequenceTests(unittest.TestCase):
    def test_float_writes_and_distance_abi_difference(self):
        cases = {
            'correction': (0.0, [0, 256, 512, 768]),
            'reflection': (25.0, [0x400, 0x500, 0x6c8, 0x741]),
            'ambient': (25.0, [0x800, 0x900, 0xac8, 0xb41]),
            'humidity': (.45, [0xc66, 0xd66, 0xee6, 0xf3e]),
            'emissivity': (1.0, [0x1000, 0x1100, 0x1280, 0x133f]),
            'distance': (1.0, [0x1400, 0x1500, 0x1680, 0x173f]),
        }
        for name, (value, expected) in cases.items():
            self.assertEqual(parameter_commands(name, value), expected)
        self.assertEqual(parameter_commands('distance', 1, official_distance=True), [0x1401, 0x1500])
        self.assertEqual(parameter_commands('correction', -1), [0, 256, 0x280, 0x3bf])
        with self.assertRaises(ValueError):
            parameter_commands('emissivity', float('nan'))

    def test_high_bits_are_counted_without_changing_pixels_or_including_trailer(self):
        raw = bytearray(FIXTURE)
        raw[:IMAGE_BYTES] = struct.pack('<4H', 0x1234, 0x5678, 0x9abc, 0xdef0) * (IMAGE_BYTES // 8)
        metrics = frame_metrics(raw)
        self.assertEqual(metrics['top_two_bits_percent'], dict.fromkeys(['00', '01', '10', '11'], 25.0))
        self.assertEqual(metrics['bit15_set_percent'], 50.0)
        self.assertEqual(metrics['bit14_set_percent'], 50.0)
        self.assertEqual(metrics['image_words_at_most_0x3fff_percent'], 25.0)
        self.assertEqual(metrics['image_word_max'], 0xdef0)
        self.assertEqual(metrics['masked_0x3fff_min_max_diagnostic_only'], [0x1234, 0x1ef0])
        self.assertEqual(metrics['masked_0x7fff_min_max_diagnostic_only'], [0x1234, 0x5ef0])
        self.assertEqual(metrics['trailer_center_index'], 5165)

    def run_experiment(self, callback, control=None):
        clock = Clock()
        control = control or Control()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            with patch('radiometric_sequence_diagnostic.time.monotonic', clock.monotonic), \
                 patch('radiometric_sequence_diagnostic.time.sleep', clock.sleep):
                experiment = Experiment(Stream(clock), control,
                                        {'commands': [], 'stages': []}, output, count=2, settle=2)
                callback(experiment)
            return experiment.report, control, json.loads(output.read_text())

    def test_capture_preserves_stage_bytes_and_refuses_overwrite(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            experiment = Experiment(Stream(clock), Control(), {'commands': [], 'stages': []},
                                    root / 'report.json', count=1, settle=1,
                                    capture_dir=root / 'captures', preserve_spatial=True)
            experiment.snapshot('A_baseline', 1)
            saved = root / 'captures/A_baseline/frame-000.raw'
            self.assertEqual(saved.read_bytes(), (Path(__file__).parent / 'fixtures/radiometric-initial.raw').read_bytes())
            self.assertEqual(len(experiment.report['stages'][0]['settling_frames']), 1)
            with self.assertRaises(FileExistsError):
                experiment.snapshot('A_baseline', 1)
            self.assertEqual(len(saved.read_bytes()), 224256)

    def test_failed_frame_before_baseline_prevents_controls(self):
        def run(experiment):
            experiment.stream.next = lambda: (_ for _ in ()).throw(RuntimeError('bad frame'))
            with self.assertRaises(RuntimeError):
                experiment.snapshot('A_baseline', 1)
                experiment.official()
        report, control, saved = self.run_experiment(run)
        self.assertEqual(control.values, [])
        self.assertEqual(saved['stages'][0]['frames'], [])

    def test_official_order_and_all_stages_persist(self):
        def run(experiment):
            experiment.snapshot('A_baseline', 2)
            experiment.official()
        report, control, saved = self.run_experiment(run)
        self.assertEqual(control.values, [32772, 32800, 32768])
        self.assertEqual([s['name'] for s in report['stages']],
                         ['A_baseline', 'B_output1', 'C_range120', 'D_refresh'])
        self.assertEqual([s['control_readback'] for s in saved['stages']], [0, 32772, 32800, 32768])
        self.assertTrue(all(len(s['frames']) == 2 for s in saved['stages']))
        self.assertTrue(all(s['discarded_frames'] == 2 for s in saved['stages']))

    def test_shutter_has_a_separate_longer_settling_window(self):
        def run(experiment):
            experiment.shutter_settle = 5
            experiment.official()
        report, _, _ = self.run_experiment(run)
        self.assertEqual([s['discarded_frames'] for s in report['stages']], [2, 2, 5])

    def test_failed_control_preserves_history_and_prevents_later_writes(self):
        def run(experiment):
            experiment.snapshot('A_baseline', 2)
            with self.assertRaises(OSError):
                experiment.official()
        report, control, saved = self.run_experiment(run, Control(fail=32800))
        self.assertEqual(control.values, [32772])
        self.assertEqual([s['name'] for s in saved['stages']], ['A_baseline', 'B_output1'])
        self.assertFalse(saved['commands'][-1]['succeeded'])
        self.assertEqual(saved['commands'][-1]['value'], 32800)

    def test_thermviewer_nested_posting_includes_overlapping_refresh(self):
        report, control, saved = self.run_experiment(lambda e: e.thermviewer())
        self.assertEqual(control.values, [32773, 0x1000, 32768, 0x1100, 0,
                                          0x1280, 0x133f, 0x100, 0x200, 0x300])
        self.assertNotIn(32800, control.values)  # SDK startup has no range call.
        self.assertEqual([round(c['planned_ms']) for c in report['commands']],
                         [20, 30, 40, 40, 40, 50, 50, 50, 60, 60])
        self.assertEqual(len(saved['stages']), 11)
        self.assertEqual(saved['stages'][-1]['name'], 'startup_settled')
        self.assertTrue(all(not s['frames'] for s in saved['stages'][:-1]))


if __name__ == '__main__':
    unittest.main()
