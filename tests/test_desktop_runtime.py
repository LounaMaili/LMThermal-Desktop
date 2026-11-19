"""User-local diagnostics must not turn a read-only input directory into an output directory."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import logging
import os
import sys
import unittest
from desktop_runtime import configure_logging


class RuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.logger=logging.getLogger('lmthermal');self.previous=list(self.logger.handlers)
        self.hook=sys.excepthook;self.logger.handlers.clear()
        if hasattr(self.logger,'diagnostic_path'):del self.logger.diagnostic_path
    def tearDown(self):
        for h in self.logger.handlers:h.close()
        self.logger.handlers=self.previous;sys.excepthook=self.hook
        if hasattr(self.logger,'diagnostic_path'):del self.logger.diagnostic_path
    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux XDG state path policy')
    def test_relative_state_override_cannot_write_logs_under_source_cwd(self):
        with TemporaryDirectory() as temp,patch('desktop_runtime.Path.home',return_value=Path(temp)),patch.dict(os.environ,{'XDG_STATE_HOME':'relative'}):
            target=configure_logging()
            self.assertTrue(target.is_absolute());self.assertTrue(target.is_relative_to(temp))
            self.assertEqual(524288,self.logger.handlers[0].maxBytes)
            self.assertEqual(2,self.logger.handlers[0].backupCount)
            self.logger.warning('synthetic diagnostic')
            self.assertIn('synthetic diagnostic',target.read_text())
            for h in self.logger.handlers:h.close()
    def test_read_only_state_falls_back_without_preventing_startup(self):
        with patch('desktop_runtime.Path.mkdir',side_effect=OSError('read-only')):
            self.assertIsNone(configure_logging())
        self.assertEqual(1,len(self.logger.handlers))
