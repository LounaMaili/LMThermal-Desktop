"""Shared Android bytes, strict container/schema attacks and generic offline data."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from celsius_palette import CelsiusRange
from lmtx_container import Container, LmtxError, safe_path
from lmtx_json import decode, dumps
from lmtx_reader import load_lmtx
from mvp_presentation import current_extrema, current_reading, native_to_widget, widget_to_native
from offline_measurement import Geometry, Rectangle, Transform, save_offline_png, statistics, freeze

CORPUS = Path(__file__).parent/'fixtures/lmtx'


def source_members(name='temperature-only.lmtx'):
    with zipfile.ZipFile(CORPUS/name) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(members.pop('manifest.json'))
    return manifest, members


def write_archive(path, manifest, members, *, refresh=True, stored=False):
    manifest = deepcopy(manifest)
    if refresh:
        for p in manifest['payloads']:
            if p['member'] in members:
                content = members[p['member']]
                p['byte_length'] = len(content)
                p['sha256'] = hashlib.sha256(content).hexdigest()
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as archive:
        for name, content in {'manifest.json': json.dumps(manifest).encode(), **members}.items():
            archive.writestr(name, content)
    return path


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'modified.lmtx'
        self.manifest, self.members = source_members()

    def tearDown(self): self.tmp.cleanup()

    def error(self, code, *, manifest=None, members=None, refresh=True):
        write_archive(self.path, self.manifest if manifest is None else manifest,
                      self.members if members is None else members, refresh=refresh)
        with self.assertRaises(LmtxError) as raised: load_lmtx(self.path)
        self.assertEqual(raised.exception.code, code, str(raised.exception))

    def test_exact_shared_corpus_hashes_and_expected_results(self):
        cases = json.loads((CORPUS/'corpus.json').read_text())['cases']
        self.assertEqual(len(cases), 11)
        for case in cases:
            with self.subTest(file=case['file']):
                content = (CORPUS/case['file']).read_bytes()
                self.assertEqual(len(content), case['archive_bytes'])
                self.assertEqual(hashlib.sha256(content).hexdigest(), case['archive_sha256'])
                if case['expected'] == 'valid':
                    capture, _ = load_lmtx(CORPUS/case['file'])
                    with zipfile.ZipFile(CORPUS/case['file']) as archive:
                        for p in capture.manifest['payloads']:
                            self.assertEqual(capture.payload_bytes[p['id']], archive.read(p['member']))
                    if capture.temperature_bytes:
                        self.assertEqual(capture.temperature_c.dtype.str, '<f4')
                        self.assertEqual(capture.temperature_c.tobytes(), capture.temperature_bytes)
                        with self.assertRaises(ValueError): capture.temperature_c.setflags(write=True)
                else:
                    with self.assertRaises(LmtxError) as raised: load_lmtx(CORPUS/case['file'])
                    self.assertEqual(raised.exception.code, case['expected'])

    def test_exact_float_bits_valid_negative_zero_and_no_raw_dependency(self):
        s, _ = load_lmtx(CORPUS/'temperature-only.lmtx')
        self.assertEqual(s.temperature_c.view('<u4').ravel().tolist(), [0x41a00000, 0x80000000, 0x41f80000, 0x41c00000])
        self.assertEqual(current_reading(s, (1,0)).native_equivalent_c, -0.0)
        self.assertIsNone(current_reading(s, (1,0)).raw14)
        self.assertIsNone(s.raw14)
        self.assertEqual(s.stats.mean_c, 18.75)
        self.assertEqual(s.roi_statistics(s.roi), s.stats)

    def test_diagnostic_metadata_exposes_module_evidence_without_mutable_aliases(self):
        source, _ = load_lmtx(CORPUS/'ht301-rich-sanitized.lmtx')
        details = source.diagnostic_metadata
        self.assertEqual(details['manifest'], source.metadata)
        self.assertIn('settings', details['extension_evidence']['org.lmthermal.camera.ht301'])
        details['extension_evidence']['org.lmthermal.camera.ht301']['settings'].clear()
        details['manifest']['source'].clear()
        self.assertTrue(source.evidence['org.lmthermal.camera.ht301']['settings'])
        self.assertTrue(source.manifest['source'])

    def test_mask_invalid_is_not_zero_reading_or_roi_extremum(self):
        s, _ = load_lmtx(CORPUS/'validity-mask.lmtx')
        self.assertIsNone(current_reading(s, (1,0)))
        self.assertEqual(s.stats.valid_pixel_count, 3)
        self.assertEqual(s.stats.mean_c, 25)
        self.assertEqual(s.stats.min_xy, (0,0))
        empty = s.roi_statistics(Rectangle(1,0,2,1))
        self.assertEqual((empty.pixel_count, empty.valid_pixel_count), (1,0))
        self.assertIsNone(empty.mean_c)
        self.assertEqual(tuple(s.render('White hot', CelsiusRange(0,40))[0,1]), (255,0,255))
        with self.assertRaises(ValueError): s.validity_mask.setflags(write=True)

    def test_no_valid_pixels_has_no_extrema_readings_or_legend_bounds(self):
        s, _ = load_lmtx(CORPUS/'no-valid-pixels.lmtx')
        self.assertFalse(s.has_readings)
        self.assertIsNone(current_reading(s, (0,0)))
        self.assertIsNone(current_extrema(s))
        self.assertIsNone(s.auto_bounds())
        self.assertTrue((s.render() == (255,0,255)).all())

    def test_alternate_geometry_and_preview_only(self):
        s, _ = load_lmtx(CORPUS/'alternate-7x19.lmtx')
        self.assertEqual(s.geometry, Geometry(7,19))
        self.assertEqual(s.point(6,18), 132)
        self.assertEqual(s.render().shape, (19,7,3))
        s, _ = load_lmtx(CORPUS/'preview-only-160x120.lmtx')
        self.assertEqual(s.geometry, Geometry(160,120))
        self.assertIsNone(s.temperature_c)
        self.assertIsNone(s.roi_statistics(Rectangle(0,0,160,120)))
        self.assertIsNone(current_reading(s, (80,60)))
        self.assertEqual(s.render().shape, (120,160,3))

    def test_optional_precise_numbers_extensions_future_shapes_palette_and_copy_isolation(self):
        s, _ = load_lmtx(CORPUS/'optional-extension-shape-palette.lmtx')
        self.assertTrue(s.palette_fallback)
        self.assertEqual(s.original_palette, 'White hot')
        self.assertEqual(s.original_palette_id, 'org.example.optional-palette')
        self.assertEqual(len(s.manifest['analysis']['shapes']), 2)
        data = s.metadata
        data['source']['model_id'] = 'changed'
        self.assertNotEqual(s.metadata['source']['model_id'], 'changed')
        with self.assertRaises(TypeError): s.manifest['source']['model_id'] = 'changed'
        optional = s.evidence['org.example.optional']
        self.assertTrue(any(isinstance(v, Decimal) for v in optional.values()))
        self.assertEqual(decode(dumps(s.metadata).encode()), s.metadata)
        newer, _ = load_lmtx(CORPUS/'newer-minor-optional.lmtx')
        self.assertEqual(newer.manifest['schema_version']['minor'], 7)
        self.assertEqual(decode(dumps(newer.metadata).encode()), newer.metadata)

    def test_ht_optional_metadata_additions_are_opaque_and_references_are_checked(self):
        m,members=source_members('ht301-rich-sanitized.lmtx')
        name=next(p['member'] for p in m['payloads'] if p['role']=='extension_json')
        metadata=json.loads(members[name])
        metadata['settings']['future_field']={'text':'opaque','number':1.25}
        metadata['host_configuration']['future_flag']=None
        members[name]=json.dumps(metadata).encode()
        write_archive(self.path,m,members)
        s,_=load_lmtx(self.path)
        self.assertEqual(s.evidence['org.lmthermal.camera.ht301']['settings']['future_field']['text'],'opaque')
        metadata['frame_relationship']['native_payload_id']='missing'
        members[name]=json.dumps(metadata).encode()
        self.error('invalid_payload',manifest=m,members=members)

    def test_no_valid_plane_can_retain_range_intent_without_readings(self):
        m,members=source_members('no-valid-pixels.lmtx')
        m['presentation']={'range_mode':'manual','effective_bounds':{'min':25,'max':45,'unit':'Cel'}}
        write_archive(self.path,m,members)
        s,_=load_lmtx(self.path)
        self.assertEqual(s.original_bounds,CelsiusRange(25,45))
        self.assertFalse(s.has_readings)
        self.assertIsNone(current_reading(s,(0,0)))

    def test_statistics_strided_and_tall_grids_use_native_first_ties(self):
        matrix=np.arange(65536,dtype='<f4').reshape(65536,1)
        self.assertEqual(statistics(matrix).max_xy,(0,65535))
        matrix=np.array([[0,1,2,3],[4,5,6,7],[8,9,10,11]],dtype='<f4')
        mask=np.ones(matrix.shape,dtype='u1');mask[1,2]=0
        calculated=statistics(matrix,mask,Rectangle(1,0,3,3))
        self.assertEqual((calculated.valid_pixel_count,calculated.mean_c,calculated.min_xy,calculated.max_xy),
                         (5,5.4,(1,0),(2,2)))

    def test_source_immutability_after_roi_palette_transform_and_portable_exclusive_png(self):
        path = self.root/'source.lmtx'
        path.write_bytes((CORPUS/'validity-mask.lmtx').read_bytes())
        original = path.read_bytes()
        s, _ = load_lmtx(path)
        s.roi_statistics(Rectangle(0,0,2,2))
        for palette in ('White hot','Black hot','Inferno','Iron-like','Turbo'): s.render(palette, CelsiusRange(25,45))
        rendered = replace(s, transform=Transform(90,True,False))
        target = self.root/'rendered.png'
        with patch('os.link', side_effect=AssertionError('Unix hard link')):
            save_offline_png(rendered, target, 'Turbo', CelsiusRange(25,45))
        with self.assertRaises(FileExistsError): save_offline_png(s, target, 'Turbo')
        with self.assertRaises(ValueError): save_offline_png(s, path, 'Turbo')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(s.temperature_c.tobytes(), s.temperature_bytes)

    def test_wrong_identity_version_kind_required_features_and_complete(self):
        for key, value, code in [('format','unknown','unsupported_format'), ('kind','sequence','unsupported_kind'),
                                 ('schema_version',{'major':2,'minor':0},'unsupported_major'),
                                 ('required_features',['core.still','core.native-coordinates','unknown.required'],'unsupported_required_feature'),
                                 ('complete',False,'invalid_manifest')]:
            with self.subTest(key=key):
                m = deepcopy(self.manifest); m[key] = value; self.error(code, manifest=m)
        for value in (True,1.0,-1,None):
            m = deepcopy(self.manifest); m['schema_version']['minor'] = value; self.error('invalid_manifest',manifest=m)

    def test_payload_inventory_missing_or_extra_even_optional(self):
        self.error('missing_payload', members={})
        self.error('missing_payload', members={**self.members,'data/extra.bin':b'x'})
        self.error('integrity_mismatch', members={next(iter(self.members)):b'x'}, refresh=False)
        m = deepcopy(self.manifest); m['payloads'][0]['sha256'] = '0'*64
        self.error('integrity_mismatch',manifest=m,refresh=False)

    def test_dtype_shape_endian_order_unit_and_allocation_guard(self):
        for key, value, code in [('dtype','f64','invalid_payload'),('shape',[1,4],'invalid_manifest'),
                                 ('byte_order','big','invalid_manifest'),('order','column_major','invalid_manifest'),
                                 ('unit','Kel','invalid_manifest'),('shape',[True,2],'invalid_manifest')]:
            with self.subTest(key=key):
                m = deepcopy(self.manifest); m['payloads'][0][key] = value; self.error(code,manifest=m)
        m = deepcopy(self.manifest); m['geometry']['width_px'] = 33554433
        self.error('resource_limit',manifest=m)

    def test_nonfinite_valid_temperature_and_all_noncanonical_invalid_fillers(self):
        name = next(iter(self.members))
        for bits in (0x7fc00000,0x7f800000,0xff800000):
            b = bytearray(self.members[name]); struct.pack_into('<I',b,0,bits)
            self.error('invalid_payload',members={name:bytes(b)})
        m, members = source_members('validity-mask.lmtx')
        name = m['payloads'][0]['member']
        for bits in (0x80000000,0x7fc00000,0x7f800000,0x3f800000):
            b = bytearray(members[name]); struct.pack_into('<I',b,4,bits)
            self.error('invalid_payload',manifest=m,members={**members,name:bytes(b)})
        mask = m['payloads'][1]['member']
        self.error('invalid_payload',manifest=m,members={**members,mask:b'\1\2\1\1'})
        wrong = deepcopy(m); wrong['measurement']['validity'] = 'all_valid'
        self.error('invalid_payload',manifest=wrong,members=members)

    def test_statistics_exact_counts_coordinates_ties_and_tolerance(self):
        for key, value in [('min',-1),('mean',100),('pixel_count',3),('valid_pixel_count',3),('min_xy',[0,0]),('max_xy',[1,1])]:
            m = deepcopy(self.manifest); m['measurement']['extrema'][key] = value
            self.error('invalid_payload',manifest=m)
        m = deepcopy(self.manifest); m['measurement']['extrema']['mean'] += .00005
        write_archive(self.path,m,self.members); load_lmtx(self.path)
        m = deepcopy(self.manifest); m['analysis']['shapes'][0]['statistics']['mean'] += .01
        self.error('invalid_payload',manifest=m)
        s = statistics(np.array([[0.,-0.,3.],[3.,3.,0.]],dtype='<f4'))
        self.assertEqual((s.min_xy,s.max_xy),((0,0),(2,0)))
        values = np.array([[2**60,1.,-(2**60),3.]],dtype='<f4')
        self.assertEqual(statistics(values).mean_c,.75) # sequential binary64, not pairwise/math.fsum.

    def test_points_clock_capability_extensions_presentation_and_unknown_null(self):
        m = deepcopy(self.manifest)
        m['analysis']['points'] = [{'id':'point','coordinate_space':'native','x_px':0,'y_px':0,'temperature_c':20}]
        write_archive(self.path,m,self.members); load_lmtx(self.path)
        m['analysis']['points'][0]['temperature_c']=21; self.error('invalid_payload',manifest=m)
        for change in ('clock','capability','null','range','transform','point','namespace'):
            with self.subTest(change=change):
                m = deepcopy(self.manifest)
                if change == 'clock': m['creation_time']['utc']='2026-01-01T00:00:00Z'
                if change == 'capability': m['capabilities']['temperature']='unsupported'
                if change == 'null': m['analysis']=None
                if change == 'range': m['presentation']={'range_mode':'auto'}
                if change == 'transform': m['presentation']={'transform':{'rotation_degrees':45,'mirror_x':False,'mirror_y':False}}
                if change == 'point': m['analysis']['points']=[{'id':'point','coordinate_space':'native','x_px':2,'y_px':0}]
                if change == 'namespace': m['extensions']=[{'id':'org.bad','schema_version':{'major':1,'minor':0},'metadata_payload_id':'missing'}]
                self.error('invalid_manifest',manifest=m)
        m = deepcopy(self.manifest); m['future_optional']=None
        write_archive(self.path,m,self.members); self.assertIsNone(load_lmtx(self.path)[0].manifest['future_optional'])

    def test_lineage_without_parent_is_structurally_valid_not_claimed_verified(self):
        m = deepcopy(self.manifest)
        m['lineage']={'operation':'analysis','parents':[{'capture_id':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','archive_sha256':'0'*64}],
                      'preserved_payloads':[{'parent_payload_id':'temperature','payload_id':'temperature'}]}
        write_archive(self.path,m,self.members); self.assertIn('lineage',load_lmtx(self.path)[0].manifest)
        m['lineage']['parents'][0]['capture_id']=m['capture_id']; self.error('invalid_manifest',manifest=m)

    def test_images_dimensions_decoding_exif_and_malformed_crc(self):
        m, members = source_members('preview-only-160x120.lmtx')
        image_member = m['payloads'][0]['member']
        b = bytearray(members[image_member]); b[-5] ^= 1
        self.error('integrity_mismatch',manifest=m,members={image_member:bytes(b)})
        m['payloads'][0]['image']['width_px']=159; m['geometry']['width_px']=159
        self.error('invalid_payload',manifest=m,members=members)
        m, members = source_members('preview-only-160x120.lmtx')
        stream = BytesIO(); exif = Image.Exif(); exif[274]=6
        Image.new('RGB',(160,120)).save(stream,format='JPEG',exif=exif)
        m['payloads'][0]['media_type']='image/jpeg'
        self.error('invalid_payload',manifest=m,members={image_member:stream.getvalue()})
        stream=BytesIO();Image.new('RGB',(160,120)).save(stream,format='JPEG')
        write_archive(self.path,m,{image_member:stream.getvalue()}); s,_=load_lmtx(self.path)
        self.assertEqual(s.render().shape,(120,160,3))

    def test_local_resource_refusal_is_explicit(self):
        with patch('lmtx_reader.LOCAL_OWNED_BYTES',15): self.error('resource_limit')
        m,members=source_members('preview-only-160x120.lmtx')
        with patch('lmtx_reader.LOCAL_IMAGE_PIXELS',19199): self.error('resource_limit',manifest=m,members=members)

    def test_inclusive_manifest_size_and_high_compression_ratio(self):
        m,members=source_members()
        text=json.dumps(m).encode()
        with zipfile.ZipFile(self.path,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',text+b' '*(1048576-len(text)))
            for n,b in members.items():z.writestr(n,b)
        load_lmtx(self.path)
        with zipfile.ZipFile(self.path,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',text+b' '*(1048577-len(text)))
            for n,b in members.items():z.writestr(n,b)
        with self.assertRaises(LmtxError) as raised:load_lmtx(self.path)
        self.assertEqual(raised.exception.code,'resource_limit')
        payload=b'\0'*1048576
        m['payloads'].append({'id':'opaque','member':'data/opaque.bin','role':'optional_evidence','media_type':'application/octet-stream',
                              'byte_length':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
        write_archive(self.path,m,{**members,'data/opaque.bin':payload})
        s,_=load_lmtx(self.path)
        self.assertEqual(s.payload_bytes['opaque'],payload)
        self.assertLess(self.path.stat().st_size, len(payload)//100)

    def test_member_count_boundary_and_sparse_archive_size_refusal(self):
        m,members=source_members()
        for i in range(254):
            name=f'data/extra-{i}.bin'; members[name]=b'x'
            m['payloads'].append({'id':f'extra-{i}','member':name,'role':'optional_evidence','media_type':'application/octet-stream',
                                  'byte_length':1,'sha256':hashlib.sha256(b'x').hexdigest()})
        write_archive(self.path,m,members); s,_=load_lmtx(self.path)
        self.assertEqual(len(s.payload_bytes),255)
        with zipfile.ZipFile(self.path,'a') as z:z.writestr('data/extra.bin',b'x')
        with self.assertRaises(LmtxError) as raised:load_lmtx(self.path)
        self.assertEqual(raised.exception.code,'resource_limit')
        with self.path.open('wb') as f:f.truncate(268435457)
        with self.assertRaises(LmtxError) as raised:load_lmtx(self.path)
        self.assertEqual(raised.exception.code,'resource_limit')

    def test_representative_large_supported_grid_exact_bits_and_explicit_local_limit(self):
        m,members=source_members()
        matrix=np.arange(1024*1024,dtype='<f4').reshape(1024,1024)
        m['geometry']['width_px']=1024; m['geometry']['height_px']=1024
        m['payloads'][0]['shape']=[1024,1024]
        m['measurement'].pop('extrema'); m.pop('analysis')
        name=m['payloads'][0]['member']
        write_archive(self.path,m,{name:matrix.tobytes()})
        source,_=load_lmtx(self.path)
        self.assertEqual(source.temperature_c.tobytes(),matrix.tobytes())
        self.assertEqual(source.stats.mean_c,524287.5)
        self.assertEqual(source.render('Turbo',CelsiusRange(0,1048576)).shape,(1024,1024,3))
        with patch('lmtx_reader.LOCAL_GRID_PIXELS',1048575):
            with self.assertRaises(LmtxError) as raised:load_lmtx(self.path)
            self.assertEqual(raised.exception.code,'resource_limit')

    def test_importer_does_not_import_camera_or_thermometry(self):
        code = '''import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name in ('ht301_camera','mvp_camera_worker','radiometric_session','native_equivalent_thermometry'):
  raise AssertionError('Forbidden camera/thermometry import: '+name)
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from lmtx_reader import load_lmtx
load_lmtx('tests/fixtures/lmtx/ht301-rich-sanitized.lmtx')
'''
        result = subprocess.run([sys.executable,'-c',code],cwd=CORPUS.parents[2],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_host_paths_with_spaces_and_unicode_keep_source_and_archive_rules(self):
        root = self.root / 'Windows path with spaces é'
        root.mkdir()
        path = root / 'capture é.lmtx'
        original = (CORPUS / 'validity-mask.lmtx').read_bytes()
        path.write_bytes(original)
        source, _ = load_lmtx(path)
        bits = source.temperature_c.tobytes()
        for palette in ('Turbo', 'White hot'):
            destination = root / f'rendered {palette} é.png'
            save_offline_png(source, destination, palette, CelsiusRange(25, 45))
            self.assertTrue(destination.exists())
        self.assertEqual(source.temperature_c.tobytes(), bits)
        self.assertEqual(path.read_bytes(), original)
        # Host Unicode/drive paths never relax the ASCII archive member rules.
        for member in ('data/é.bin', 'C:/data/temperature.bin'):
            with self.assertRaises(LmtxError):
                safe_path(member)

    def test_viewer_entry_import_does_not_load_linux_acquisition(self):
        code = '''import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name in ('ht301_camera','mvp_camera_worker','radiometric_sequence_diagnostic','fcntl'):
  raise AssertionError('Forbidden acquisition import: '+name)
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from lmthermal_viewer import MainWindow
from PyQt6.QtWidgets import QApplication
import time
app=QApplication([])
window=MainWindow(auto_connect=False)
assert window.open_lmtx('tests/fixtures/lmtx/ht301-rich-sanitized.lmtx')
deadline=time.monotonic()+5
while window.offline_capture is None and time.monotonic()<deadline:
 app.processEvents()
 time.sleep(.002)
assert window.offline_capture is not None, window.state_label.text()
assert window.worker is None
window.close()
'''
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        result = subprocess.run([sys.executable, '-c', code], cwd=CORPUS.parents[2],
                                capture_output=True, text=True, env=environment, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.path=Path(self.tmp.name)/'test.lmtx'
        m,members=source_members();write_archive(self.path,m,members,stored=True)
        self.original=self.path.read_bytes()

    def tearDown(self): self.tmp.cleanup()

    def rejected(self, content, code='invalid_container'):
        self.path.write_bytes(content)
        with self.assertRaises(LmtxError) as raised: load_lmtx(self.path)
        self.assertEqual(raised.exception.code,code,str(raised.exception))

    def test_all_windows_unsafe_paths_and_inclusive_path_limit(self):
        for p in ('/data/a','C:/data/a','data\\a','data/../a','data/a..b','data/A','data/con.bin','data/aux',
                  'data/com9.txt','data/lpt1.dat','data/a.','data//a','data/a\x00b','extensions/é/a','other/a'):
            with self.subTest(path=p),self.assertRaises(LmtxError):safe_path(p)
        self.assertEqual(safe_path('data/'+'a'*235),'data/'+'a'*235)
        with self.assertRaises(LmtxError) as raised:safe_path('data/'+'a'*236)
        self.assertEqual(raised.exception.code,'resource_limit')

    def test_zip_truncation_trailing_preamble_encrypted_zip64_method_offsets_crc(self):
        self.rejected(self.original[:-1]);self.rejected(self.original+b'x');self.rejected(b'X'+self.original)
        central=self.original.index(b'PK\1\2')
        for offset, value, kind in [(central+8,1,'H'),(central+10,12,'H'),(central+42,1,'I'),(central+24,0xffffffff,'I'),
                                   (8,8,'H'),(14,42,'I'),(central+4,45,'H')]:
            if offset==central+4:continue # creator version alone is not ZIP64.
            b=bytearray(self.original);struct.pack_into('<'+kind,b,offset,value)
            self.rejected(bytes(b))
        b=bytearray(self.original)
        name_length,extra_length=struct.unpack_from('<2H',b,26)
        b[30+name_length+extra_length]^=1
        self.rejected(bytes(b),'integrity_mismatch')

    def test_duplicate_case_collision_parent_conflicts_directory_links_executables(self):
        m,members=source_members()
        for name in ('data/x','data/x/y'):
            members[name]=b'x'
        write_archive(self.path,m,members)
        self.rejected(self.path.read_bytes(),'unsafe_path')
        for attr in (0o120777<<16,0o100755<<16,0x10,0o020600<<16):
            with zipfile.ZipFile(self.path,'w') as z:
                info=zipfile.ZipInfo('manifest.json');info.external_attr=attr;z.writestr(info,b'{}')
            self.rejected(self.path.read_bytes())
        with zipfile.ZipFile(self.path,'w') as z:
            z.writestr('manifest.json',b'{}');z.writestr('manifest.json',b'{}')
        self.rejected(self.path.read_bytes(),'unsafe_path')

    def test_valid_signed_and_unsigned_data_descriptors_and_deflate(self):
        class NonSeekable(BytesIO):
            def seek(self,*args):raise OSError('nonseekable producer')
        for method in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            target=NonSeekable();m,members=source_members()
            with zipfile.ZipFile(target,'w',compression=method) as z:
                for n,b in {'manifest.json':json.dumps(m).encode(),**members}.items():z.writestr(n,b)
            self.path.write_bytes(target.getvalue()); load_lmtx(self.path)
            # Unsigned descriptors require rebasing each subsequent offset/directory.
            content=bytearray(target.getvalue()); central=content.index(b'PK\1\2')
            offsets=[];at=central
            while content[at:at+4]==b'PK\1\2':
                offset=struct.unpack_from('<I',content,at+42)[0]
                nl,xl=struct.unpack_from('<2H',content,offset+26)
                compressed=struct.unpack_from('<I',content,at+20)[0]
                offsets.append(offset+30+nl+xl+compressed)
                nl,xl,cl=struct.unpack_from('<3H',content,at+28);at+=46+nl+xl+cl
            for pos in reversed(offsets):del content[pos:pos+4]
            newcentral=central-4*len(offsets);at=newcentral
            for index in range(len(offsets)):
                old=struct.unpack_from('<I',content,at+42)[0];struct.pack_into('<I',content,at+42,old-index*4)
                nl,xl,cl=struct.unpack_from('<3H',content,at+28);at+=46+nl+xl+cl
            struct.pack_into('<I',content,at+16,newcentral)
            self.path.write_bytes(content);load_lmtx(self.path)

    def test_json_bom_duplicates_nonfinite_depth_strings_items_and_numbers(self):
        for content in (b'\xef\xbb\xbf{}',b'{"a":1,"a":2}',b'{"n":NaN}',b'{"n":Infinity}',b'{}x',b'{"s":"\\ud800"}',b'[]',b'\xff'):
            with self.subTest(content=content),self.assertRaises(LmtxError):decode(content)
        for content in (b'{"a":'+b'['*32+b'0'+b']'*32+b'}',json.dumps({'a':'x'*16385}).encode(),
                        json.dumps({'a':[0]*65537}).encode()):
            with self.assertRaises(LmtxError) as raised:decode(content)
            self.assertEqual(raised.exception.code,'resource_limit')
        precise=b'{"a":1.12345678901234567890123456789,"b":1e1000}'
        self.assertEqual(decode(dumps(decode(precise)).encode()),decode(precise))


class TransformTests(unittest.TestCase):
    def test_all_quarter_turn_mirror_combinations_invert_at_resizes(self):
        geometry=Geometry(7,19)
        for rotation in (0,90,180,270):
            for mx in (False,True):
                for my in (False,True):
                    transform=Transform(rotation,mx,my)
                    for size in ((800,600),(100,100),(600,900)):
                        for point in ((0,0),(6,18),(3,9)):
                            p=native_to_widget(*point,*size,geometry=geometry,transform=transform)
                            self.assertEqual(widget_to_native(*p,*size,geometry=geometry,transform=transform),point)
                    grid=np.arange(133).reshape(19,7)
                    visible=transform.image(grid)
                    for y in range(19):
                        for x in range(7):
                            dx,dy=transform.forward(x+.5,y+.5,geometry)
                            self.assertEqual(visible[int(dy),int(dx)],grid[y,x])


if __name__=='__main__':unittest.main()
