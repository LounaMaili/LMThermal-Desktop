"""Independent integration review regressions: malformed presentation must not reach Qt."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from lmtr_test_support import base_plan,rehash_segment
from lmtr_reference.container import write_plan
from lmtr_reader import LmtrPlaybackModel
from lmtr_reference import ContractError


def visual_plan(channels,encoding):
    """A standard visual ID with an inconsistent channel count exposes the adapter boundary."""
    plan=base_plan();item=plan['chunks'][0];meta=item['metadata'];s=meta['segments'][0]['streams'][0]
    s['capabilities']['preview']='supported';s['encodings'].append({'role':'preview','encoding_id':encoding})
    s['policy'].update(level='full',requested_roles=['preview','temperature'])
    e=meta['entries'][1];e['retention']['preview']={'status':'present'}
    p=deepcopy(e['payloads'][0]);p.update(id='preview',role='preview',block_id='b2',length=str(6*channels),
        encoding_id=encoding,dtype='u8',byte_order='not_applicable',shape=[2,3] if channels==1 else [2,3,channels],unit=None)
    e['payloads'].append(p)
    meta['blocks'].append(dict(id='b2',role='preview',encoding_id=encoding,dtype='u8',byte_order='not_applicable',
        offset='0',decoded=str(6*channels),stored=str(6*channels),codec=0,hash='0'*64))
    item['payload_recipes']['1:preview']={'kind':'hex','hex':('12345678'*6)[:12*channels]}
    rehash_segment(meta);return plan


class Phase3ReaderTests(unittest.TestCase):
    def test_inconsistent_standard_visual_channels_refused_before_ui_render(self):
        for encoding,channels in [('org.lmthermal.visual.rgb8-v1',1),('org.lmthermal.visual.rgba8-v1',3),
                                  ('org.lmthermal.visual.gray8-v1',4)]:
            with self.subTest(encoding=encoding),TemporaryDirectory() as temp:
                path=Path(temp)/'hostile-preview.lmtr';write_plan(visual_plan(channels,encoding),path)
                model=LmtrPlaybackModel(path)
                try:
                    with self.assertRaises(ContractError) as error:model.frame(1)
                    self.assertEqual('invalid_payload',error.exception.code)
                finally:model.close()
    def test_standard_visual_channels_are_display_only_and_exact(self):
        for encoding,channels in [('org.lmthermal.visual.rgb8-v1',3),('org.lmthermal.visual.rgba8-v1',4),
                                  ('org.lmthermal.visual.gray8-v1',1)]:
            with self.subTest(encoding=encoding),TemporaryDirectory() as temp:
                path=Path(temp)/'preview.lmtr';write_plan(visual_plan(channels,encoding),path)
                model=LmtrPlaybackModel(path)
                try:
                    frame=model.frame(1)
                    self.assertEqual(18,len(frame.preview_bytes));self.assertEqual(20,frame.point(0,0))
                    self.assertEqual(6*channels,len(frame.payload_bytes['preview']))
                finally:model.close()
    @unittest.skipIf(__import__('os').name=='nt','Linux atomic replacement with an open descriptor; Windows checked separately')
    def test_same_size_timestamp_path_replacement_refuses_old_descriptor_publication(self):
        import os
        from unittest.mock import patch
        from lmtr_test_support import FILES
        with TemporaryDirectory() as temp:
            path=Path(temp)/'source.lmtr';replacement=Path(temp)/'replacement.lmtr'
            original=(FILES/'illustration-01-stored.lmtr').read_bytes()
            path.write_bytes(original);replacement.write_bytes(original)
            st=path.stat();os.utime(replacement,ns=(st.st_atime_ns,st.st_mtime_ns))
            model=LmtrPlaybackModel(path);decode=model.reader.decode
            def swapped(record):
                result=decode(record);os.replace(replacement,path);return result
            try:
                with patch.object(model.reader,'decode',side_effect=swapped):
                    with self.assertRaisesRegex(ValueError,'source_changed'):model.frame(1)
                self.assertEqual(0,model.cache_bytes);self.assertEqual(original,path.read_bytes())
            finally:model.close()
