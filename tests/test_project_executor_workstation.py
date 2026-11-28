from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
import project_executor_workstation as workstation


class WorkstationTests(unittest.TestCase):
    def test_metadata_detects_changes_without_reading_file_or_link_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config'
            path.write_text('synthetic secret')
            before = workstation.path_metadata(path)
            path.chmod(0o600)
            self.assertNotEqual(before, workstation.path_metadata(path))
            path.unlink()
            path.symlink_to('/missing')
            with patch.object(Path, 'open', side_effect=AssertionError('content read')):
                self.assertIsNotNone(workstation.path_metadata(path))

    @patch.object(workstation, 'snapshot')
    def test_drift_is_checked_even_when_the_fixture_fails(self, snapshot):
        snapshot.side_effect = [{'services': 'original'}, {'services': 'restarted'}]
        with self.assertRaisesRegex(RuntimeError, 'changed during acceptance: services'):
            with workstation.protect_workstation():
                raise RuntimeError('synthetic fixture error')
        self.assertEqual(snapshot.call_count, 2)

    @patch.object(workstation, 'snapshot')
    def test_incomplete_baseline_prevents_fixture_creation(self, snapshot):
        snapshot.side_effect = RuntimeError('Cannot confirm current state')
        with self.assertRaisesRegex(RuntimeError, 'Cannot confirm current state'):
            with workstation.protect_workstation():
                self.fail('The fixture started without a baseline')

    @patch.object(workstation, 'run')
    def test_optional_service_absence_is_explicit(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, stdout=(
            'Id=nginx.service\nLoadState=loaded\nActiveState=active\nInvocationID=abcd\n\n'
            'Id=mariadb.service\nLoadState=not-found\nActiveState=inactive\n'
        ))
        states = workstation.unit_states(('nginx.service', 'mariadb.service'))
        self.assertEqual(states['mariadb.service']['LoadState'], 'not-found')
        with self.assertRaisesRegex(RuntimeError, 'Cannot confirm all'):
            workstation.unit_states(('nginx.service', 'mariadb.service', 'redis-server.service'))

    @patch.object(workstation, 'run')
    def test_unknown_service_invocation_is_refused(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout='Id=nginx.service\nLoadState=loaded\nActiveState=active\n')
        with self.assertRaisesRegex(RuntimeError, 'Cannot identify an active'):
            workstation.unit_states(('nginx.service',))


if __name__ == '__main__':
    unittest.main()
