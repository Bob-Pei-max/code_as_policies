"""Offline checks for source provenance and safe, reproducible preparation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import bootstrap_source
import build_env
import download_assets

ROOT = Path(__file__).resolve().parents[1]


class SourceTests(unittest.TestCase):
    def test_vendored_files_match_pinned_hashes(self):
        manifest = json.loads((ROOT / 'upstream.json').read_text(encoding='utf-8'))
        names = [item['file'] for item in manifest['files']]
        self.assertIn('LICENSE', names)
        self.assertEqual(len(names), len(set(names)))
        for record in manifest['files']:
            with self.subTest(file=record['file']):
                data = (ROOT / 'code_as_policies' / record['file']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'])

    def test_generated_environment_is_reproducible(self):
        self.assertEqual(build_env.DESTINATION.read_text(encoding='utf-8'), build_env.render_source())

    def test_modified_notebook_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'code_as_policies').mkdir()
            (root / 'upstream.json').write_bytes((ROOT / 'upstream.json').read_bytes())
            (root / 'code_as_policies' / 'Interactive_Demo.ipynb').write_text('{}', encoding='utf-8')
            with patch.object(build_env, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    build_env.render_source()

    def test_valid_source_bootstrap_needs_no_network(self):
        with patch('bootstrap_source.urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            bootstrap_source.main()


class AssetTests(unittest.TestCase):
    def test_archive_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'asset.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('model/mesh.txt', 'original')
            original = archive_path.read_bytes()
            record = {'bytes': len(original), 'sha256': hashlib.sha256(original).hexdigest()}
            download_assets.verify_archive(archive_path, record)
            archive_path.write_bytes(original + b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                download_assets.verify_archive(archive_path, record)

    def test_archive_cannot_write_outside_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / 'assets'
            destination.mkdir()
            archive_path = root / 'unsafe.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('valid.txt', 'should not be extracted either')
                archive.writestr('../escaped.txt', 'unsafe')
            with self.assertRaisesRegex(ValueError, 'unsafe path'):
                download_assets.extract_archive(archive_path, destination)
            self.assertEqual(list(destination.iterdir()), [])
            self.assertFalse((root / 'escaped.txt').exists())


if __name__ == '__main__':
    unittest.main()
