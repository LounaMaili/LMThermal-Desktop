"""Private bundle verification refuses path escapes and changed bytes without executing the artifact."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.audit_desktop_bundle import audit


class PackagingAuditTests(unittest.TestCase):
    def test_own_inventory_verifies_and_changed_payload_is_refused(self):
        with TemporaryDirectory() as name:
            root=Path(name);folder=root/'bundle';folder.mkdir();item=folder/'runtime.bin';item.write_bytes(b'example')
            manifest=root/'sha.txt';manifest.write_text(hashlib.sha256(item.read_bytes()).hexdigest()+'  runtime.bin\n')
            self.assertEqual(1,audit(folder,manifest)['verified_files'])
            item.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'mismatch'):audit(folder,manifest)
    def test_inventory_cannot_follow_a_parent_or_external_link(self):
        with TemporaryDirectory() as name:
            root=Path(name);folder=root/'bundle';folder.mkdir();outside=root/'outside';outside.write_bytes(b'preserve')
            manifest=root/'sha.txt';digest=hashlib.sha256(outside.read_bytes()).hexdigest()
            manifest.write_text(digest+'  ../outside\n')
            with self.assertRaisesRegex(ValueError,'escapes'):audit(folder,manifest)
            try:(folder/'link').symlink_to(outside)
            except (OSError,NotImplementedError):return  # Parent-path check also runs on unprivileged Windows.
            manifest.write_text(digest+'  link\n')
            with self.assertRaisesRegex(ValueError,'escapes'):audit(folder,manifest)
            self.assertEqual(b'preserve',outside.read_bytes())
