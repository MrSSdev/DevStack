"""Framework detection is read-only and does not execute project code."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'project_frameworks', Path(__file__).resolve().parents[1] / 'daemon/project_frameworks.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class FrameworkTests(unittest.TestCase):
    def test_installed_metadata(self):
        laravel = {'name': 'laravel/framework', 'version': 'v13.42.0'}
        self.assertEqual(reader.parse_installed({'packages': [laravel]}),
                         {'name': 'Laravel', 'version': '13.42.0'})
        self.assertEqual(reader.parse_installed([{'name': 'symfony/framework-bundle', 'version': 'v8.1.2'}]),
                         {'name': 'Symfony', 'version': '8.1.2'})
        self.assertIsNone(reader.parse_installed({'packages': [{'name': 'laravel/installer', 'version': '5.33.1'}]}))
        self.assertEqual(reader.parse_installed([{'name': 'laravel/framework', 'version': '<script>'}]),
                         {'name': 'Laravel', 'version': None})
        self.assertIsNone(reader.parse_installed({'packages': [{'name': []}, None]}))
        self.assertIsNone(reader.parse_installed({'packages': {}}))
        self.assertIsNone(reader.parse_installed(None))

    def test_live_versions_and_invalid_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            vendor = base / 'demo/vendor/composer'
            vendor.mkdir(parents=True)
            file = vendor / 'installed.json'
            for version in ('13.42.0', '13.43.1'):
                file.write_text(json.dumps({'packages': [{'name':'laravel/framework','version':version}]}))
                self.assertEqual(reader.read_project(base, 'demo'), {'name':'Laravel','version':version})
            file.write_text('{')
            self.assertIsNone(reader.read_project(base, 'demo'))
            with file.open('wb') as handle:
                handle.truncate(reader.MAX_METADATA + 1)
            self.assertIsNone(reader.read_project(base, 'demo'))
            file.unlink()
            os.mkfifo(file)
            self.assertIsNone(reader.read_project(base, 'demo'))
            self.assertIsNone(reader.read_project(base, '../demo'))
            self.assertIsNone(reader.read_project(base, 'missing'))

    def test_refuse_symlinks_at_every_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / 'outside'
            (outside / 'vendor/composer').mkdir(parents=True)
            actual = outside / 'vendor/composer/installed.json'
            actual.write_text(json.dumps([{'name':'laravel/framework','version':'13.42.0'}]))
            (base / 'demo').symlink_to(outside, target_is_directory=True)
            self.assertIsNone(reader.read_project(base, 'demo'))
            (base / 'other').mkdir()
            (base / 'other/vendor').symlink_to(outside / 'vendor', target_is_directory=True)
            self.assertIsNone(reader.read_project(base, 'other'))
            (base / 'third/vendor').mkdir(parents=True)
            (base / 'third/vendor/composer').symlink_to(outside / 'vendor/composer', target_is_directory=True)
            self.assertIsNone(reader.read_project(base, 'third'))
            (base / 'fourth/vendor/composer').mkdir(parents=True)
            (base / 'fourth/vendor/composer/installed.json').symlink_to(actual)
            self.assertIsNone(reader.read_project(base, 'fourth'))


if __name__ == '__main__':
    unittest.main()
