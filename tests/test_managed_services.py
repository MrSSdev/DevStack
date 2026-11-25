import importlib.util
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('managed_services_test', ROOT/'daemon/managed_services.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ManagedServiceTests(unittest.TestCase):
    def setUp(self):
        self.rows = m.catalog()
        self.values = {r['unit']: {'Id': r['unit'], 'LoadState': 'loaded', 'ActiveState': 'active'} for r in self.rows}
        self.values['redis-server.service']['ActiveState'] = 'inactive'

    def test_catalog_and_installed_only_not_just_running(self):
        self.assertEqual([r['id'] for r in self.rows], ['nginx','php','mariadb','redis','mailpit'])
        self.values['devstack-mailpit.service']['LoadState'] = 'not-found'
        with patch.object(m, 'units_snapshot', return_value=self.values),patch.object(m,'selection',return_value=['php']):
            rows = m.public_rows()
            self.assertEqual([r['id'] for r in rows], ['nginx','php','mariadb','redis'])
            self.assertEqual(next(r['state'] for r in rows if r['id']=='redis'),'inactive')
            self.assertEqual([r['id'] for r in rows if r['widget_selected']],['php'])

    def test_selection_atomic_independent_and_empty_allowed(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'SELECTION',pathlib.Path(tmp)/'selection.json'):
            self.assertEqual(m.selection(self.rows),['nginx','php','mariadb','redis'])
            self.assertEqual(m.save_selection(['mailpit','nginx'],self.rows),['nginx','mailpit'])
            self.assertEqual(m.selection(self.rows),['nginx','mailpit'])
            self.assertEqual(m.SELECTION.stat().st_mode&0o777,0o644)
            for bad in ('mailpit',['sshd'],['mailpit','mailpit'],[None],{},[[]]):
                with self.assertRaises(ValueError):m.save_selection(bad,self.rows)
            self.assertEqual(m.selection(self.rows),['nginx','mailpit'])
            self.assertEqual(m.save_selection([],self.rows),[])
            self.assertEqual(m.selection(self.rows),[])
            m.SELECTION.write_text('invalid')
            self.assertEqual(m.selection(self.rows),['nginx','php','mariadb','redis'])

    def test_status_distinguishes_missing_optional_and_bus_failure(self):
        self.values['devstack-mailpit.service']['LoadState']='not-found'
        output='\n\n'.join('\n'.join(f'{k}={v}' for k,v in row.items()) for row in self.values.values())
        with patch.object(m.subprocess,'run',return_value=SimpleNamespace(stdout=output,returncode=1)):
            self.assertEqual(m.units_snapshot(self.rows),self.values)
        with patch.object(m.subprocess,'run',return_value=SimpleNamespace(stdout='',returncode=1)):
            with self.assertRaises(RuntimeError):m.units_snapshot(self.rows)

    def test_only_fixed_actions_and_installed_unit_are_scheduled(self):
        with patch.object(m,'units_snapshot',return_value=self.values),patch.object(m,'action_pending',return_value=False),patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            for action in ('start','stop','restart'):
                result=m.schedule('mailpit',action)
                self.assertTrue(result['accepted'])
                self.assertEqual(run.call_args.args[0][-4:],['/usr/bin/systemctl','--no-ask-password',action,'devstack-mailpit.service'])
            for service,action in [('sshd','restart'),('mailpit.service','restart'),('nginx','reset-failed'),('nginx','--force'),([], 'restart')]:
                with self.assertRaises(ValueError):m.schedule(service,action)
            self.values['devstack-mailpit.service']['LoadState']='not-found'
            with self.assertRaises(ValueError):m.schedule('mailpit','start')
            self.assertEqual(run.call_count,3)
        with patch.object(m,'units_snapshot',return_value=self.values),patch.object(m,'action_pending',return_value=True),patch.object(m.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):m.schedule('nginx','restart')
            run.assert_not_called()
