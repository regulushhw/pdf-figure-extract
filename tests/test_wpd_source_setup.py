import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wpd_source_setup import PATHS, setup


class WpdSourceSetupTests(unittest.TestCase):
    def test_copy_checks_all_inputs_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            files = []
            for name, relative in PATHS.items():
                data = name.encode('utf-8')
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                files.append({'file': name, 'sha256': hashlib.sha256(data).hexdigest()})
            manifest = root / 'checksums.json'
            manifest.write_text(json.dumps({'files': files}), encoding='utf-8')
            bad = source / next(iter(PATHS.values()))
            old = bad.read_bytes()
            bad.write_bytes(b'bad')
            with self.assertRaises(ValueError):
                setup(source, root / 'output', manifest)
            self.assertFalse((root / 'output').exists())
            bad.write_bytes(old)
            self.assertEqual(setup(source, root / 'output', manifest)['verified_files'], len(PATHS))
            with self.assertRaises(ValueError):
                setup(source, root / 'output', manifest)
            with self.assertRaises(ValueError):
                setup(source, source / 'output', manifest)


if __name__ == '__main__':
    unittest.main()
