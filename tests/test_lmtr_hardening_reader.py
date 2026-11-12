"""Local resource admission, exact ownership and hostile/recovery boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import json
import weakref
import unittest

from lmtr_reader import LmtrPlaybackModel, LmtrReadLimits, _DesktopReader
from lmtr_reference import ContractError
from lmtr_reference.container import write_plan
from lmtr_test_support import FILES, segmented_plan, opaque_plan, gap_plan


class LmtrHardeningReaderTests(unittest.TestCase):
    def model(self,name='illustration-01-stored',**kwargs):
        model=LmtrPlaybackModel(FILES/(name+'.lmtr'),**kwargs);self.addCleanup(model.close);return model
    def test_decoded_budget_rejects_before_inflater_or_numerical_decode(self):
        model=self.model('illustration-01-deflate1',limits=LmtrReadLimits(decoded_cache_bytes=1))
        with patch('lmtr_reference.container.inflate',side_effect=AssertionError('must not decompress')):
            with self.assertRaises(ContractError) as error: model.frame(1)
        self.assertEqual('local_resource_limit',error.exception.code)
        self.assertEqual(0,model.cache_bytes)
    def test_working_budget_rejects_body_before_its_large_read(self):
        real=_DesktopReader.read;requests=[]
        def limited(reader,offset,length):
            requests.append(length)
            self.assertLessEqual(length,5000,'must reject before reading oversized CHUNK body')
            return real(reader,offset,length)
        with patch.object(_DesktopReader,'read',limited),self.assertRaises(ContractError) as error:
            self.model(limits=LmtrReadLimits(working_bytes=5000))
        self.assertEqual('local_resource_limit',error.exception.code)
        self.assertTrue(requests)
    def test_returned_entry_and_header_cannot_mutate_verified_cache(self):
        model=self.model();expected=model.frame(1).temperature_bytes
        entry=model.original_entry(1);entry['payloads'][0]['offset']='4';entry['measurement']['status']='unsupported'
        header=model.header;header['required_features'].append('org.example.unknown')
        self.assertEqual(expected,model.frame(1).temperature_bytes)
        self.assertEqual('available',model.original_entry(1)['measurement']['status'])
        self.assertNotIn('org.example.unknown',model.header['required_features'])
    def test_close_drops_cache_navigation_and_model_has_no_owned_self_cycle(self):
        model=LmtrPlaybackModel(FILES/'illustration-01-stored.lmtr');model.frame(1)
        facade=model.entries;ref=weakref.ref(model)
        model.close();self.assertTrue(model.reader.file.closed)
        self.assertEqual(0,model.cache_bytes);self.assertEqual({},model.reader.cache)
        self.assertEqual({},model.reader.logical)
        del model
        self.assertIsNone(ref(),'owned facades must not keep a closed model alive')
        with self.assertRaises(ReferenceError):len(facade)
    def test_full_verification_evicts_previous_decoded_chunk_first(self):
        model=self.model();model.frame(1);self.assertGreater(model.cache_bytes,0)
        original=model.reader.check
        def checked(*args,**kwargs):
            self.assertEqual(0,model.cache_bytes)
            return original(*args,**kwargs)
        with patch.object(model.reader,'check',checked): report=model.verify_full()
        self.assertEqual('full',report['integrity_scope'])
    def test_observed_mid_operation_source_change_refuses_publication(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'changing.lmtr';path.write_bytes((FILES/'illustration-01-stored.lmtr').read_bytes())
            model=LmtrPlaybackModel(path)
            try:
                real=model.reader.decode
                def changed(record):
                    result=real(record)
                    with path.open('ab') as output:output.write(b'x')
                    return result
                with patch.object(model.reader,'decode',changed),self.assertRaisesRegex(ValueError,'source_changed'):model.frame(1)
                self.assertEqual(0,model.cache_bytes)
            finally:model.close()
    def test_segment_sessions_and_geometry_preserve_native_planes(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'segments.lmtr';write_plan(segmented_plan(),path)
            model=LmtrPlaybackModel(path)
            try:
                self.assertEqual('full',model.verify_full()['integrity_scope'])
                self.assertEqual((2,3),model.frame(1).geometry.shape)
                self.assertEqual((4,7),model.frame(4).geometry.shape)
                self.assertEqual(25,model.frame(4).point(6,3))
                self.assertEqual(1,model.cache_size)
                with self.assertRaises(IndexError):model.frame(True)
            finally:model.close()
    def test_opaque_no_grid_preserves_evidence_without_fake_temperature(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'opaque.lmtr';write_plan(opaque_plan(),path);before=path.read_bytes()
            model=LmtrPlaybackModel(path)
            try:
                self.assertEqual('full',model.verify_full()['integrity_scope'])
                self.assertIsNone(model.frame(1))
                data=model.read_payloads(1)
                self.assertEqual(bytes.fromhex('00112233445566778899aabbccddeeff'),data['acquisition'])
                with self.assertRaises(TypeError):data['acquisition']=b'changed'
                self.assertEqual(before,path.read_bytes())
            finally:model.close()
    def test_known_gap_is_not_an_observation_or_a_reused_matrix(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'gap.lmtr';write_plan(gap_plan(),path)
            model=LmtrPlaybackModel(path)
            try:
                report=model.verify_full()
                self.assertEqual('1',report['summary']['known_missing_observations'])
                self.assertIsNotNone(model.frame(1));self.assertIsNone(model.frame(2))
                self.assertEqual('gap',model.entries[2].kind)
                self.assertEqual({},model.read_payloads(2))
            finally:model.close()
    def test_vendored_algorithms_and_registry_match_pinned_source_hashes(self):
        package=Path(__file__).resolve().parents[1]/'lmtr_reference'
        receipt=json.loads((package/'PROVENANCE.json').read_text())
        for name,expected in receipt['files'].items():
            source=(package/name).read_bytes()
            if name=='jsoncodec.py':source=source.replace(b"Path(__file__).with_name('schema.json')",b"Path(__file__).resolve().parents[2] / 'docs/lmtr-v1/schema.json'")
            self.assertEqual(expected,hashlib.sha256(source).hexdigest(),name)
        self.assertEqual(receipt['schema_sha256'],hashlib.sha256((package/'schema.json').read_bytes()).hexdigest())
    def test_different_native_grid_remains_exact_evidence_not_primary_pixel_raw(self):
        from copy import deepcopy
        from lmtr_test_support import base_plan,rehash_segment
        plan=base_plan();item=plan['chunks'][0];meta=item['metadata'];stream=meta['segments'][0]['streams'][0]
        stream['capabilities']['native_samples']='supported'
        stream['policy'].update(level='full',requested_roles=['native_samples','temperature'])
        stream['encodings'].append({'role':'native_samples','encoding_id':'org.lmthermal.ht301.raw14'})
        entry=meta['entries'][1];entry['retention']['native_samples']={'status':'present'}
        native=deepcopy(entry['payloads'][0]);native.update(id='native_samples',role='native_samples',block_id='b2',length='4',
            encoding_id='org.lmthermal.ht301.raw14',dtype='u16',shape=[1,2],unit=None)
        native['geometry'].update(width_px=2,height_px=1)
        entry['payloads'].append(native)
        meta['blocks'].append({'id':'b2','role':'native_samples','encoding_id':native['encoding_id'],
            'dtype':'u16','byte_order':'little','offset':'0','stored':'4','decoded':'4','codec':0,'hash':'0'*64})
        item['payload_recipes']['1:native_samples']={'kind':'hex','hex':'34127816'}
        rehash_segment(meta)
        with TemporaryDirectory() as temp:
            path=Path(temp)/'native-grid.lmtr';write_plan(plan,path)
            model=LmtrPlaybackModel(path)
            try:
                frame=model.frame(1)
                self.assertEqual((2,3),frame.geometry.shape)
                self.assertIsNone(frame.raw14)
                self.assertEqual((1,2),frame.native_planes[0].array.shape)
                self.assertEqual(bytes.fromhex('34127816'),frame.payload_bytes['native_samples'])
                self.assertEqual(26,frame.point(2,1))
            finally:model.close()
