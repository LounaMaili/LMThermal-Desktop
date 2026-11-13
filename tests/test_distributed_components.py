"""Native provenance follows actual COLLECT entries and refuses invented/escaping inputs."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from tools.distributed_components import collected_members, write_native_inventory


class DistributedComponentTests(unittest.TestCase):
    def test_actual_native_members_retain_both_hashes_and_unknown_origin(self):
        with TemporaryDirectory() as name:
            root=Path(name);folder=root/'app';(folder/'_internal').mkdir(parents=True);(folder/'THIRD_PARTY').mkdir()
            source=root/'original.so';source.write_bytes(b'original')
            (folder/'_internal/example.so').write_bytes(b'rpath rewritten')
            toc=root/'COLLECT.toc';toc.write_text(repr(([('example.so',str(source),'BINARY'),('not-native','ignored','DATA')],)))
            report=write_native_inventory(folder,toc,distributions=[],use_dpkg=False)
            self.assertEqual((1,1),(report['native_members'],report['unmapped']))
            member=report['members'][0];self.assertNotEqual(member['source_sha256'],member['delivered_sha256'])
            self.assertEqual(report,json.loads((folder/'THIRD_PARTY/native-components.json').read_text()))

    def test_collected_escape_and_missing_member_are_refused(self):
        with TemporaryDirectory() as name:
            root=Path(name);toc=root/'COLLECT.toc'
            toc.write_text(repr(([('../escape','source','BINARY')],)))
            with self.assertRaisesRegex(ValueError,'escapes'):list(collected_members(toc))
            toc.write_text(repr(([('missing','source','EXTENSION')],)))
            with self.assertRaisesRegex(ValueError,'Missing'):write_native_inventory(root,toc,distributions=[],use_dpkg=False)
