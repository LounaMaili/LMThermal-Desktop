"""Invalid diagnostic requests must exit before a Qt platform/camera can be initialized."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]


class WindowCheckCliTests(unittest.TestCase):
    def test_invalid_input_and_existing_or_in_source_output_are_rejected_before_qt(self):
        with TemporaryDirectory() as name:
            root=Path(name);source=root/'source.lmthermal';source.mkdir();report=root/'report';report.mkdir()
            cases=[['--expect-window-error'],['--window-check-output',str(root/'new')],
                   ['--open',str(root/'absent.lmtx'),'--window-check-output',str(root/'new')],
                   ['--open',str(source),'--legacy-recording','--window-check-output',str(source/'inside')],
                   ['--open',str(ROOT/'tests/fixtures/lmtx/temperature-only.lmtx'),'--window-check-output',str(report)]]
            for args in cases:
                result=subprocess.run([sys.executable,str(ROOT/'lmthermal_desktop.py'),*args],
                    env={**os.environ,'QT_QPA_PLATFORM':'unavailable-test-platform'},capture_output=True,text=True,timeout=10)
                self.assertEqual(2,result.returncode,result.stderr)
                self.assertIn('error:',result.stderr)
