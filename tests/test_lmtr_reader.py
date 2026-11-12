"""Pinned R3 corpus plus offline adapter/cache/source-integrity regressions."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lmtr_reference import Reader, ContractError
from lmtr_reader import LmtrPlaybackModel, MAX_CACHE_BYTES
from offline_measurement import Rectangle, save_offline_png
from celsius_palette import CelsiusRange

ROOT = Path(__file__).parent/'fixtures/lmtr'


class LmtrReaderTests(unittest.TestCase):
    def model(self, name='illustration-01-stored', **kwargs):
        model = LmtrPlaybackModel(ROOT/'files'/f'{name}.lmtr',**kwargs)
        self.addCleanup(model.close)
        return model
    def test_all_51_original_hashes_and_full_reference_verdicts(self):
        manifest=json.loads((ROOT/'manifest.json').read_text())
        self.assertEqual(51,len(manifest['cases']))
        for case in manifest['cases']:
            with self.subTest(case=case['id']):
                path=ROOT/case['file']; before=path.read_bytes()
                self.assertEqual(case['sha256'],hashlib.sha256(before).hexdigest())
                self.assertEqual(case['bytes'],len(before))
                expected=case['expected']
                try:
                    with Reader(path) as reader:
                        result=reader.check(full=True,recover=case.get('recover',False))
                    self.assertNotEqual('error',expected['status'])
                    self.assertEqual(expected,result)
                except ContractError as error:
                    self.assertEqual('error',expected['status'])
                    self.assertEqual(expected['error'],error.code)
                self.assertEqual(before,path.read_bytes())
    def test_lazy_open_selected_mask_and_float_bits(self):
        model=self.model()
        self.assertEqual('opened_records',model.report['integrity_scope'])
        self.assertEqual(0,model.cache_bytes)
        self.assertIsNone(model.frame(0))
        frame=model.frame(1)
        self.assertEqual('0000a0410000f04100000080000000000000c0410000d041',frame.temperature_bytes.hex())
        self.assertIsNone(frame.point(0,1))
        self.assertEqual(30,frame.point(1,0))
        self.assertEqual(20,frame.stats.mean_c)
        self.assertEqual(1,model.cache_size)
        self.assertLessEqual(model.cache_bytes,MAX_CACHE_BYTES)
        self.assertFalse(frame.temperature_c.flags.writeable)
        with self.assertRaises(ValueError): frame.temperature_c.setflags(write=True)
    def test_all_positive_sources_allow_every_event_and_owned_frame(self):
        manifest=json.loads((ROOT/'manifest.json').read_text())
        for case in manifest['cases']:
            if case['classification']!='positive': continue
            with self.subTest(case=case['id']):
                model=self.model(case['id'])
                for index in range(len(model.entries)):
                    entry=model.entries[index]; frame=model.frame(index)
                    if entry.state!='observation': self.assertIsNone(frame)
                    elif frame.temperature_c is not None:
                        self.assertEqual(frame.geometry.shape,frame.temperature_c.shape)
                    self.assertLessEqual(model.cache_bytes,MAX_CACHE_BYTES)
    def test_incomplete_is_explicit_unknown_tail_not_completed(self):
        with self.assertRaises(ContractError): self.model('missing-end')
        model=self.model('missing-end',recover=True)
        self.assertFalse(model.completed)
        self.assertTrue(model.report['unknown_tail'])
        self.assertEqual('full_verified_prefix',model.report['integrity_scope'])
        self.assertEqual(30,model.frame(1).point(1,0))
    def test_recovery_never_hides_committed_source_or_required_feature_damage(self):
        for name in ('committed-body-corrupt','committed-logical-hash-corrupt','unknown-required-feature'):
            with self.subTest(name=name),self.assertRaises(ContractError): self.model(name,recover=True)
    def test_navigation_damage_uses_verified_recovery_not_fictitious_end(self):
        model=self.model('committed-index-damage',recover=True)
        self.assertTrue(model.recovered)
        self.assertEqual('navigation_rebuilt',model.report['reason'])
        self.assertIsNotNone(model.frame(1))
    def test_native_only_never_creates_temperatures(self):
        frame=self.model('native-only-big-endian-nonraw14').frame(1)
        self.assertIsNone(frame.temperature_c);self.assertFalse(frame.has_readings)
        self.assertIsNone(frame.raw14);self.assertTrue(frame.native_planes)
    def test_roi_palette_png_and_exact_source_preservation(self):
        model=self.model(); frame=model.frame(1); before=model.path.read_bytes()
        roi=Rectangle(0,0,2,1)
        values=frame.roi_statistics(roi)
        for palette in ('White hot','Inferno','Turbo'):
            self.assertEqual(values,frame.roi_statistics(roi))
            self.assertEqual((2,3,3),frame.render(palette,CelsiusRange(0,40)).shape)
        with TemporaryDirectory() as root:
            target=Path(root)/'export é space.png'
            save_offline_png(frame,target,'Turbo',CelsiusRange(0,40))
            self.assertTrue(target.read_bytes().startswith(b'\x89PNG'))
            with self.assertRaises(FileExistsError): save_offline_png(frame,target,'Turbo')
        self.assertEqual(before,model.path.read_bytes())
    def test_exact_time_navigation_gaps_and_bounds(self):
        model=self.model()
        self.assertEqual(1,model.seek_time_ns(1).sequence)
        self.assertIsNone(model.seek_time_ns(2000001))
        self.assertEqual(2,model.seek(999).index)
        self.assertEqual(0,model.step(-999).index)
        with self.assertRaises(IndexError): model.entries[-1]
        with self.assertRaises(ValueError): model.seek_time_ns(-1)
    def test_source_change_is_refused_even_for_cached_chunk(self):
        with TemporaryDirectory() as root:
            path=Path(root)/'copy.lmtr'; path.write_bytes((ROOT/'files/illustration-01-stored.lmtr').read_bytes())
            model=LmtrPlaybackModel(path); self.addCleanup(model.close);model.frame(1)
            path.write_bytes(path.read_bytes()+b'x')
            with self.assertRaisesRegex(ValueError,'source_changed'): model.frame(1)
    def test_cancellation_closes_without_publishing_partial_measurement(self):
        with self.assertRaises(ContractError) as error:
            self.model(cancelled=lambda:True)
        self.assertEqual('cancelled',error.exception.code)
