import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from project_executor import Session, sandbox_argv, socket_filter, validate_tree
from project_mounts import PinnedProject


class ProjectExecutorTests(unittest.TestCase):
    def test_only_protected_request_fields_select_the_fixture_and_profile(self):
        session = Session(Path('/unused'), '1' * 32)
        request = {'schema': 1, 'session_id': '1' * 32, 'project_id': '2' * 32,
                   'agent_id': 'fixture-agent', 'profile': 'read',
                   'argv': ['/usr/bin/git', 'status']}
        with patch('project_executor.read_control_json', return_value=request):
            self.assertEqual(session.request(), request)
        for changes in ({'session_id': '3' * 32}, {'project_id': '../other'},
                        {'agent_id': 'agent /'}, {'profile': 'production'},
                        {'path': '/home/ubuntu'}, {'argv': ['/workspace/script']},
                        {'argv': ['/usr/bin/sh', 'a\0b']}, {'schema': True}):
            with self.subTest(changes=changes), patch(
                    'project_executor.read_control_json', return_value={**request, **changes}):
                with self.assertRaises(ValueError):
                    session.request()
        with self.assertRaises(ValueError):
            Session(Path('/unused'), '../bad')
        with self.assertRaises(ValueError):
            session.service_argv(runtime_seconds=0)

    def test_profiles_use_the_same_pinned_sources_and_policy(self):
        request = {'profile': 'read', 'argv': ['/usr/bin/git', 'status']}
        read = sandbox_argv(4, 5, 6, 7, request)
        write = sandbox_argv(4, 5, 6, 7, {**request, 'profile': 'write-cli'})
        self.assertEqual(read[read.index('/workspace') - 2:read.index('/workspace')],
                         ['--ro-bind-fd', '4'])
        self.assertEqual(write[write.index('/workspace') - 2:write.index('/workspace')],
                         ['--bind-fd', '4'])
        self.assertEqual([value for value in read if value != '--ro-bind-fd'],
                         [value for value in write if value not in ('--ro-bind-fd', '--bind-fd')])
        command = read[read.index('--') + 1:]
        self.assertEqual(command[:4], ['/usr/bin/python3', '-I', '-S', '-c'])
        self.assertEqual(command[5:], ['/usr/bin/git', 'status'])

    def test_whole_fixture_checks_nodes_at_any_depth(self):
        mapping = SimpleNamespace(developer=(os.getuid(), os.getgid()))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            child = project / 'child'
            child.mkdir(mode=0o700)
            (child / 'original').touch(mode=0o600)
            with PinnedProject(project) as pinned:
                validate_tree(pinned, mapping)
                invalid = child / 'unwanted'
                for create in (lambda: invalid.symlink_to('/root'),
                               lambda: os.link(child / 'original', invalid),
                               lambda: os.mkfifo(invalid)):
                    create()
                    with self.assertRaises(ValueError):
                        validate_tree(pinned, mapping)
                    invalid.unlink()
                (child / '.GIT').touch()
                with self.assertRaises(ValueError):
                    validate_tree(pinned, mapping)

    def test_socket_filter_exports_bpf_to_cloexec_fd(self):
        fd = socket_filter()
        try:
            self.assertFalse(os.get_inheritable(fd))
            self.assertGreater(os.fstat(fd).st_size, 0)
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
        finally:
            os.close(fd)


if __name__ == '__main__':
    unittest.main()
