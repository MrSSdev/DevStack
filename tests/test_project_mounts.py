from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from project_mounts import PinnedProject, components, decode_mount_path, mount_id


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'storage').mkdir()
        (self.project / 'storage/marker').write_text('own')
        self.other = self.base / 'other'
        self.other.mkdir()
        (self.other / 'marker').write_text('other')

    def test_components_and_mountinfo_escapes(self):
        self.assertEqual(components('storage/app/public'), ['storage', 'app', 'public'])
        self.assertEqual(decode_mount_path(r'/a\040b\134c'), '/a b\\c')
        for bad in ('/storage', 'storage/../other', 'storage//app', '.', 'a\\b'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                components(bad)

    def test_pinned_descriptor_survives_rename_and_replacement(self):
        with PinnedProject(self.project) as mounts:
            args = mounts.bind('storage', writable=True)
            fd = int(args[1])
            self.assertEqual(args[0], '--bind-fd')
            self.assertFalse(os.get_inheritable(fd))
            (self.project / 'storage').rename(self.project / 'old-storage')
            (self.project / 'storage').symlink_to(self.other, target_is_directory=True)
            with os.fdopen(os.open('marker', os.O_RDONLY, dir_fd=fd)) as stream:
                self.assertEqual(stream.read(), 'own')
            with self.assertRaises(OSError):
                mounts.bind('storage')
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_optional_missing_never_accepts_symlink(self):
        (self.project / 'outside').symlink_to(self.other, target_is_directory=True)
        with PinnedProject(self.project) as mounts:
            self.assertEqual(mounts.bind('missing', optional=True), [])
            with self.assertRaises(OSError):
                mounts.bind('outside', optional=True)
            with self.assertRaises(ValueError):
                mounts.bind('../other')

    def test_mount_id_mismatch_rejected(self):
        with PinnedProject(self.project) as mounts:
            with patch('project_mounts.mount_id', return_value=mounts.device + 1):
                with self.assertRaises(ValueError):
                    mounts.bind('storage')

    def test_nested_mount_snapshot_refused(self):
        with patch('project_mounts.Path.read_text',
                   return_value='1 2 0:1 / ' + str(self.project / 'storage') + ' rw - tmpfs tmpfs rw\n'):
            with self.assertRaises(ValueError):
                with PinnedProject(self.project):
                    pass


if __name__ == '__main__':
    unittest.main()
