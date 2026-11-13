"""Private bundle verification refuses path escapes and changed bytes without executing the artifact."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.audit_desktop_bundle import audit
from tools.audit_desktop_bundle import elf_requirements
from unittest.mock import patch


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

    def test_unlisted_files_and_duplicate_members_are_refused(self):
        with TemporaryDirectory() as name:
            root=Path(name);folder=root/'bundle';folder.mkdir();item=folder/'one';item.write_bytes(b'one')
            line=hashlib.sha256(item.read_bytes()).hexdigest()+'  one\n'
            manifest=root/'sha.txt';manifest.write_text(line)
            (folder/'unlisted').write_bytes(b'not audited')
            with self.assertRaisesRegex(ValueError,'omits'):audit(folder,manifest)
            (folder/'unlisted').unlink();manifest.write_text(line+line)
            with self.assertRaisesRegex(ValueError,'Duplicate'):audit(folder,manifest)

    def test_provided_cpp_versions_are_not_misrepresented_as_required(self):
        output='Version definition section .gnu.version_d\nName: GLIBCXX_3.4.99\nVersion needs section .gnu.version_r\nName: GLIBC_2.35\nName: GLIBCXX_3.4.29\nName: CXXABI_1.3.13\nName: GLIBC_ABI_DT_RELR'
        self.assertEqual(['CXXABI_1.3.13','GLIBCXX_3.4.29','GLIBC_2.35','GLIBC_ABI_DT_RELR'],elf_requirements(output))

    def test_elf_cannot_be_silently_skipped_without_readelf(self):
        with TemporaryDirectory() as name:
            root=Path(name);folder=root/'bundle';folder.mkdir();item=folder/'elf';item.write_bytes(b'\x7fELFsample')
            manifest=root/'sha.txt';manifest.write_text(hashlib.sha256(item.read_bytes()).hexdigest()+'  elf\n')
            with patch('tools.audit_desktop_bundle.shutil.which',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'readelf'):audit(folder,manifest)
