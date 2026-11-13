"""Wrong source/platform and reused output refuse before a qualification mutates anything."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tools.qualify_native_desktop import preflight


class NativeQualificationGuards(unittest.TestCase):
    def test_wrong_source_and_dirty_source_create_no_output(self):
        with TemporaryDirectory() as name:
            output=Path(name)/'new'
            with patch('tools.qualify_native_desktop.subprocess.check_output',side_effect=['a'*40]):
                with self.assertRaisesRegex(ValueError,'Wrong source'):preflight('b'*40,output)
            with patch('tools.qualify_native_desktop.subprocess.check_output',side_effect=['a'*40,' M source']):
                with self.assertRaisesRegex(ValueError,'Dirty'):preflight('a'*40,output)
            self.assertFalse(output.exists())

    def test_linux_cannot_certify_native_windows(self):
        with patch('tools.qualify_native_desktop.sys.platform','linux'):
            with self.assertRaisesRegex(ValueError,'Native Windows'):preflight('a'*40,Path('not-created'),True)
