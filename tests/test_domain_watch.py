"""Regression tests for event-triggered WSL project state notifications."""
import importlib.util
import struct
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'devstack_watch_domains', Path(__file__).resolve().parents[1] / 'scripts/watch-domains.py')
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


def event(mask, name=b'projects.json'):
    encoded = name + b'\0'
    return struct.pack('iIII', 1, mask, 0, len(encoded)) + encoded


class DomainWatchTests(unittest.TestCase):
    def test_project_state_atomic_replace_triggers(self):
        changed, removed = watcher.parse_change_events(event(watcher.IN_MOVED_TO))
        self.assertTrue(changed)
        self.assertFalse(removed)

    def test_other_files_do_not_trigger(self):
        self.assertEqual(watcher.parse_change_events(event(watcher.IN_MOVED_TO, b'credentials.json')),
                         (False, False))
        self.assertEqual(watcher.parse_change_events(event(watcher.IN_CREATE, b'.devstack-temporary')),
                         (False, False))

    def test_watch_removal(self):
        self.assertEqual(watcher.parse_change_events(event(watcher.IN_IGNORED, b'')),
                         (False, True))

    def test_batched_events(self):
        data = event(watcher.IN_CLOSE_WRITE, b'other') + event(watcher.IN_MOVED_TO)
        self.assertEqual(watcher.parse_change_events(data), (True, False))

    def test_incomplete_event_rejected(self):
        with self.assertRaises(ValueError):
            watcher.parse_change_events(event(watcher.IN_MOVED_TO)[:-1])


if __name__ == '__main__':
    unittest.main()
