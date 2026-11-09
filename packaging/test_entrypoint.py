import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('entrypoint', Path(__file__).with_name('entrypoint.py'))
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_private_credential_rotates_without_trailing_old_bytes(self):
        path = self.root / 'credential'
        entrypoint.write_credential(path, 'old-placeholder-value')
        entrypoint.write_credential(path, 'new')
        self.assertEqual(path.read_text(), 'new')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_link_cannot_redirect_credential(self):
        target = self.root / 'retained'
        target.write_text('keep')
        link = self.root / 'credential'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            entrypoint.write_credential(link, 'placeholder')
        self.assertEqual(target.read_text(), 'keep')

    def test_hard_link_cannot_clobber_retained_file(self):
        target = self.root / 'retained'
        target.write_text('keep')
        link = self.root / 'credential'
        os.link(target, link)
        with self.assertRaises(ValueError):
            entrypoint.write_credential(link, 'placeholder')
        self.assertEqual(target.read_text(), 'keep')

    def test_fifo_does_not_block_startup(self):
        import subprocess
        path = self.root / 'credential'
        os.mkfifo(path)
        code = "import runpy,sys; runpy.run_path(sys.argv[1])['write_credential'](sys.argv[2], 'placeholder')"
        result = subprocess.run([os.sys.executable, '-c', code, str(Path(entrypoint.__file__)), str(path)],
                                capture_output=True, timeout=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))
