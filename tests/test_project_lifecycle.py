from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from project_lifecycle import Component, LifecycleError, parse_snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.component = Component('php', 'dsrt-a-0123456789', Path('/unused/php.sock'))
        self.values = {'Id': self.component.unit, 'LoadState': 'loaded',
                       'ActiveState': 'active', 'Transient': 'yes',
                       'User': self.component.user, 'Description': self.component.description}

    def parse(self, values, code=0):
        result = subprocess.CompletedProcess([], code,
            stdout=''.join(f'{k}={v}\n' for k, v in values.items()), stderr='')
        return parse_snapshot(result, self.component)

    def test_running_and_stopped(self):
        self.assertEqual(self.parse(self.values), 'active')
        self.assertEqual(self.parse({**self.values, 'ActiveState': 'failed'}), 'failed')
        self.assertEqual(self.parse({'Id': self.component.unit,
                         'LoadState': 'not-found', 'ActiveState': 'inactive'}, 1), 'absent')

    def test_fail_closed_on_unowned_or_unknown_unit(self):
        for key, value in [('Description', 'foreign'), ('Transient', 'no'),
                           ('User', 'root'), ('Id', 'nginx.service'),
                           ('ActiveState', 'unknown'), ('LoadState', 'error')]:
            with self.subTest(key=key):
                with self.assertRaises(LifecycleError):
                    self.parse({**self.values, key: value})
        for values, code in [({}, 1), (self.values, 1),
                             ({'Id': self.component.unit, 'LoadState': 'not-found'}, 1)]:
            with self.assertRaises(LifecycleError):
                self.parse(values, code)

    def test_stop_never_controls_unowned_unit(self):
        from project_lifecycle import stop_component
        with patch('project_lifecycle.unit_state', side_effect=LifecycleError('foreign')), \
             patch('project_lifecycle.run') as run:
            with self.assertRaises(LifecycleError):
                stop_component(self.component)
            run.assert_not_called()


    def test_probe_errors_are_not_absence(self):
        from project_lifecycle import socket_probe
        from types import SimpleNamespace
        with patch('project_lifecycle.pwd.getpwnam',
                   return_value=SimpleNamespace(pw_uid=33, pw_gid=33)), \
             patch('project_lifecycle.run') as run:
            for code, expected in ((0, True), (10, False)):
                run.return_value = subprocess.CompletedProcess([], code, stdout='', stderr='')
                self.assertIs(socket_probe(self.component), expected)
            for code in (1, 2, 126, 127, -9):
                run.return_value = subprocess.CompletedProcess([], code, stdout='', stderr='')
                with self.assertRaises(LifecycleError):
                    socket_probe(self.component)
