import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from project_access import AccessError, LinuxACL, acl_plan, inspect_tree
from project_mounts import PinnedProject, mount_id


class ProjectAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'project'
        self.project.mkdir(mode=0o700)
        self.file = self.project / 'source.php'
        self.file.write_text('fixture')
        self.file.chmod(0o600)
        self.outside = self.base / 'outside'
        self.outside.write_text('outside')
        self.outside.chmod(0o600)

    def inspect(self):
        with PinnedProject(self.project) as pinned:
            return inspect_tree(pinned.root, os.geteuid(), os.getegid(),
                                pinned.device, mount_id)

    def test_links_are_not_followed_and_hardlinks_are_refused(self):
        (self.project / 'symlink').symlink_to(self.outside)
        self.assertIn(('symlink',), self.inspect())
        os.link(self.outside, self.project / 'hardlink')
        with self.assertRaises(AccessError):
            self.inspect()
        self.assertEqual(self.outside.read_text(), 'outside')
        self.assertEqual(stat.S_IMODE(self.outside.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.project.stat().st_mode), 0o700)

    def test_special_nodes_and_existing_permissions_refused(self):
        fifo = self.project / 'pipe'
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(AccessError):
            self.inspect()
        fifo.unlink()
        self.file.chmod(0o644)
        with self.assertRaises(AccessError):
            self.inspect()

    def test_pinned_acl_changes_original_inode_not_replaced_path(self):
        fd = os.open(self.file, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            moved = self.project / 'moved.php'
            self.file.rename(moved)
            self.file.symlink_to(self.outside)
            acl = LinuxACL()
            acl.apply(fd, 0x8000, 'u::rw-,u:65534:r--,g::---,m::r--,o::---')
            self.assertEqual(stat.S_IMODE(moved.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(self.outside.stat().st_mode), 0o600)
            with self.assertRaises(AccessError):
                self.inspect()
        finally:
            os.close(fd)

    def test_acl_plans_preserve_owner_and_separate_writable_subtrees(self):
        source = acl_plan(self.file.stat(), ('source.php',), 1000, 900)
        self.assertEqual(source, ((0x8000, 'u::rw-,u:900:r--,g::---,m::r--,o::---'),))
        target = acl_plan(self.file.stat(), ('database', 'fixture.sqlite'), 1000, 900)
        self.assertIn('u:900:rw-', target[0][1])
        directory = acl_plan(self.project.stat(), ('storage',), 1000, 900)
        self.assertIn('u:1000:rwx', directory[1][1])
        self.assertIn('u:900:rwx', directory[1][1])
        readonly = acl_plan(self.project.stat(), ('public',), 1000, 900)
        self.assertNotIn('u:1000:', readonly[1][1])
        self.assertIn('u:900:r-x', readonly[1][1])


if __name__ == '__main__':
    unittest.main()
