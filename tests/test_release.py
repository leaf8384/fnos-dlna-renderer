import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1] / 'scripts/update-fnpack.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class PackageValidation(unittest.TestCase):
    def package(self, folder, version, name='dlna-renderer'):
        p = Path(folder) / 'renamed.fpk'
        body = f'appname = {name}\nversion = {version}\nplatform = x86\n'.encode()
        with tarfile.open(p, 'w:gz') as t:
            entry = tarfile.TarInfo('./manifest'); entry.size = len(body)
            t.addfile(entry, io.BytesIO(body))
        return p

    def test_matching_package(self):
        with tempfile.TemporaryDirectory() as d:
            release.verify_package(self.package(d, '1.1.10'), 'dlna-renderer', '1.1.10', 'x86')

    def test_renamed_old_package_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                release.verify_package(self.package(d, '1.1.9'), 'dlna-renderer', '1.1.10', 'x86')

    def test_other_application_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                release.verify_package(self.package(d, '1.1.10', 'other'), 'dlna-renderer', '1.1.10', 'x86')

    def test_missing_current_does_not_pick_old(self):
        with tempfile.TemporaryDirectory() as d:
            self.package(d,'1.1.9')
            old=release.DIST_DIR
            try:
                release.DIST_DIR=Path(d)
                with self.assertRaises(SystemExit): release.pick_fpk(None)
            finally: release.DIST_DIR=old

if __name__ == '__main__': unittest.main()
