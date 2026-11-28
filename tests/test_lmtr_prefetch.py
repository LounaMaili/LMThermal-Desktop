"""Deflate ownership, indexed lookahead admission and reference rejection parity."""
from concurrent.futures import Future
from contextlib import closing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lmtr_reader import LmtrPlaybackModel, LmtrReadLimits, _DesktopReader, _Ticket
from lmtr_reference import ContractError, Reader
from lmtr_reference import container
from lmtr_test_support import FILES, segmented_plan
from lmtr_verified_blocks import verify_ahead


class ImmediatePool:
    """Exercise real verification with deterministic completion and no Qt child."""
    def __init__(self, **_):
        self.shutdown_called = False

    def submit(self, call, *args):
        future = Future()
        future.set_result(call(*args))
        return future

    def shutdown(self, *, wait, cancel_futures):
        self.shutdown_called = wait and cancel_futures


class PrefetchTests(unittest.TestCase):
    def setUp(self):
        pool = patch('lmtr_reader.ProcessPoolExecutor', ImmediatePool)
        pool.start()
        self.addCleanup(pool.stop)

    def open(self, source):
        model = LmtrPlaybackModel(source)
        model.reader._parallel = True
        return closing(model)

    def test_verified_blocks_are_inflated_once_and_unchanged_when_reused(self):
        source = FILES / 'illustration-01-deflate1.lmtr'
        with self.open(source) as model, patch.object(container, 'inflate', wraps=container.inflate) as inflate:
            frame = model.frame(1)
            _, meta, _ = model._chunk(1)
            count = sum(b['codec'] == 1 for b in meta['blocks'])
            self.assertGreater(count, 0)
            self.assertEqual(count, inflate.call_count)
            self.assertEqual(frame.payload_bytes, model.read_payloads(1))
            self.assertEqual('0000a0410000f04100000080000000000000c0410000d041', frame.temperature_bytes.hex())
            self.assertEqual('010101000101', frame.mask_bytes.hex())
            self.assertEqual(count, inflate.call_count)

    def test_next_chunk_is_verified_once_and_retained_inside_the_speculative_budget(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'segments.lmtr'
            container.write_plan(segmented_plan(), source)
            with self.open(source) as model:
                model.seek(1)
                with patch('lmtr_reader.LOOKAHEAD_CHUNKS', 1):
                    self.assertTrue(model.prepare_playback())
                pool = model.reader._pool
                self.assertIsNotNone(pool)
                self.assertEqual(1, len(model.reader._tickets))
                self.assertEqual(1, len(model.reader.selected))
                with patch('lmtr_reader.decode_verified_blocks', side_effect=AssertionError('repeated validation')):
                    frame = model.frame(4)
                self.assertEqual(25, frame.point(6, 3))
                self.assertEqual('4000000', model.original_entry(4)['timeline_ns'])
                self.assertEqual(0, len(model.reader._tickets))
                self.assertEqual(2, len(model.reader.selected))
            self.assertTrue(pool.shutdown_called)
            self.assertEqual(0, model.cache_bytes)
            self.assertTrue(model.reader.file.closed)

    def test_changed_file_is_refused_even_with_a_completed_lookahead_result(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'segments.lmtr'
            container.write_plan(segmented_plan(), source)
            with self.open(source) as model:
                model.seek(1)
                self.assertTrue(model.prepare_playback())
                self.assertTrue(model.reader._tickets)
                previous = source.stat()
                with source.open('r+b') as file:
                    file.seek(-1, os.SEEK_END)
                    final = file.read(1)
                    file.seek(-1, os.SEEK_END)
                    file.write(bytes([final[0] ^ 1]))
                os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                with self.assertRaisesRegex(ValueError, 'source_changed'):
                    model.frame(4)
                self.assertEqual(1, len(model.reader.selected))

    def test_speculative_budget_refusal_falls_back_to_normal_verification(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'segments.lmtr'
            container.write_plan(segmented_plan(), source)
            with self.open(source) as model, patch('lmtr_reader.LOOKAHEAD_BYTES', 1):
                model.seek(1)
                self.assertTrue(model.prepare_playback())
                self.assertIsNone(model.reader._pool)
                self.assertEqual(25, model.frame(4).point(6, 3))

    def test_completed_corrupt_prefetch_has_the_same_rejection_as_independent_r3(self):
        cases = ('nonbinary-validity-mask', 'negative-zero-invalid-filler', 'nonfinite-valid-temperature',
                 'committed-decoded-byte-corrupt', 'committed-logical-hash-corrupt')
        for name in cases:
            source = FILES / (name + '.lmtr')
            # These canonical negatives intentionally end after their committed
            # CHUNK. Compare that exact payload through both verification paths.
            with (self.subTest(name=name), Reader(source) as reference,
                  _DesktopReader(source, LmtrReadLimits()) as reader):
                with self.assertRaises(ContractError) as expected:
                    reference.decode(reference.record(reference.header_end, 2))
                record = reader.record(reader.header_end, 2)
                metadata = reader.metadata(record)
                charge = reader._admit(record, metadata)
                future = ImmediatePool().submit(verify_ahead, record['body'], metadata, reader.head)
                reader._tickets[record['offset']] = _Ticket(record, future, charge)
                reader.capture_blocks = True
                with self.assertRaises(ContractError) as actual:
                    reader.decode(record)
                self.assertEqual(expected.exception.code, actual.exception.code)
                self.assertEqual(expected.exception.detail, actual.exception.detail)
                self.assertIsNone(reader.captured_blocks)
                self.assertEqual(set(), reader.selected)
