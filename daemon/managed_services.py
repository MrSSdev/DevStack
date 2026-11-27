"""Root-deployed service registry shared by the daemon and widget bridge.

Only explicitly registered application services can be controlled.  The public
selection contains non-secret service IDs, not project state or credentials.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sqlite3
import sys
import time
import uuid
from contextlib import closing

CATALOG = Path(__file__).resolve().with_name('service-catalog.json')
SELECTION = Path('/var/lib/devstack-widget-selection.json')
SYSTEMCTL = '/usr/bin/systemctl'
ACTION_UNIT = 'devstack-service-control'
RESULTS_DB = Path('/var/lib/devstack/service-results.sqlite3')
RESULT_HELPER = Path(__file__).resolve()
RESULT_LIMIT = 200
RESULT_TTL = 30 * 86400
ACTIONS = frozenset({'start', 'stop', 'restart'})
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'SYSTEMD_PAGER': '', 'SYSTEMD_COLORS': '0'}
ID = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')
UNIT = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\.service\Z')


def catalog():
    if CATALOG.stat().st_size > 32768:
        raise ValueError('Invalid service catalog')
    rows = json.loads(CATALOG.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not 1 <= len(rows) <= 24:
        raise ValueError('Invalid service catalog')
    ids, units = set(), set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id', 'unit', 'name', 'description', 'probe', 'port', 'widget_default', 'panel_dependency'}
                or not isinstance(row['id'], str) or not ID.fullmatch(row['id']) or row['id'] in ids
                or not isinstance(row['unit'], str) or not UNIT.fullmatch(row['unit']) or row['unit'] in units
                or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 40 or not row['name'].isprintable()
                or not isinstance(row['description'], str) or not 1 <= len(row['description']) <= 150 or not row['description'].isprintable()
                or row['probe'] not in ('https', 'tcp', 'unit')
                or type(row['port']) is not int or not 0 <= row['port'] <= 65535
                or (row['probe'] == 'tcp' and row['port'] == 0)
                or type(row['widget_default']) is not bool or type(row['panel_dependency']) is not bool):
            raise ValueError('Invalid service catalog entry')
        ids.add(row['id']); units.add(row['unit'])
    return rows


def units_snapshot(rows, metrics=False, *, autostart=False):
    props = 'Id,LoadState,ActiveState'
    if autostart:
        props += ',UnitFileState'
    if metrics:
        props += ',MemoryCurrent,CPUUsageNSec'
    result = subprocess.run([SYSTEMCTL, 'show', '--no-pager', '--property='+props, *[r['unit'] for r in rows]],
                            capture_output=True, text=True, timeout=3, env=ENV, check=False)
    if len(result.stdout) > 65536:
        raise RuntimeError('Service status response too large')
    parsed = {}
    allowed = {r['unit'] for r in rows}
    for block in result.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if values.get('Id') in allowed:
            parsed[values['Id']] = values
    # A missing optional unit may make systemctl exit nonzero. It must still
    # have an explicit not-found result; an unavailable bus is not "stopped".
    if set(parsed) != allowed:
        raise RuntimeError('Nie mozna odczytac stanu zarejestrowanych uslug.')
    return parsed


def installed_rows(rows, snapshot):
    return [r for r in rows if snapshot[r['unit']].get('LoadState') in ('loaded', 'masked', 'error', 'bad-setting')]


def selection(rows):
    defaults = [r['id'] for r in rows if r['widget_default']]
    try:
        if not SELECTION.exists():
            return defaults
        if SELECTION.is_symlink() or SELECTION.stat().st_size > 4096:
            raise ValueError('Invalid selection file')
        value = json.loads(SELECTION.read_text(encoding='utf-8'))
        if not isinstance(value, list) or len(value) > 24 or any(not isinstance(v, str) or not ID.fullmatch(v) for v in value) or len(set(value)) != len(value):
            raise ValueError('Invalid selection file')
        return [r['id'] for r in rows if r['id'] in value]
    except (OSError, ValueError):
        # Only a presentation preference: malformed/missing data may safely
        # fall back to the original four defaults, not grant control access.
        return defaults


def save_selection(value, rows):
    allowed = {r['id'] for r in rows}
    if (not isinstance(value, list) or len(value) > 24
            or any(not isinstance(v, str) or v not in allowed for v in value)
            or len(set(value)) != len(value)):
        raise ValueError('Wybierz tylko zainstalowane uslugi DevStack.')
    ordered = [r['id'] for r in rows if r['id'] in value]
    fd, tmp = tempfile.mkstemp(prefix='.devstack-widget-selection-', dir=SELECTION.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(ordered, handle)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, SELECTION)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return ordered


def autostart_status(row, unit):
    """Panel dependencies start with DevStack; optional units use persistent enablement."""
    if unit.get('LoadState') != 'loaded':
        return None, False
    if row['panel_dependency']:
        return True, False
    state = unit.get('UnitFileState')
    if state in ('enabled', 'disabled'):
        return state == 'enabled', True
    # Do not equate masked, static, runtime-only, or unknown units with disabled.
    return None, False


def public_rows():
    rows = catalog()
    values = units_snapshot(rows, autostart=True)
    installed = installed_rows(rows, values)
    selected = selection(rows)
    result = []
    for row in installed:
        unit = values[row['unit']]
        enabled, editable = autostart_status(row, unit)
        result.append({**row, 'state': unit.get('ActiveState', 'unknown')[:30],
                       'load_state': unit.get('LoadState', 'unknown')[:30],
                       'widget_selected': row['id'] in selected,
                       'autostart': enabled, 'autostart_editable': editable})
    return result


def set_autostart(service, enabled):
    """Change persistent systemd enablement, never current process state."""
    if not isinstance(service, str) or type(enabled) is not bool:
        raise ValueError('Nieprawidlowe ustawienie autostartu.')
    target = next((r for r in catalog() if r['id'] == service), None)
    if target is None or target['panel_dependency']:
        raise ValueError('Autostart tej uslugi nie moze byc zmieniany.')
    if action_pending():
        raise RuntimeError('Trwa juz operacja uslugi. Odswiez stan.')
    current, editable = autostart_status(target, units_snapshot([target], autostart=True)[target['unit']])
    if not editable:
        raise ValueError('Autostart tej uslugi jest niedostepny.')
    if current != enabled:
        action = 'enable' if enabled else 'disable'
        try:
            result = subprocess.run([SYSTEMCTL, '--no-ask-password', action, '--', target['unit']],
                                    capture_output=True, timeout=12, env=ENV, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('Nie udalo sie potwierdzic ustawienia autostartu. Odswiez stan.') from None
        if result.returncode:
            raise RuntimeError('Nie udalo sie zapisac autostartu. Odswiez stan.')
        actual, _ = autostart_status(target, units_snapshot([target], autostart=True)[target['unit']])
        if actual is not enabled:
            raise RuntimeError('Nie udalo sie potwierdzic ustawienia autostartu. Odswiez stan.')
    return {'service': service, 'autostart': enabled}


def action_pending():
    result = subprocess.run([SYSTEMCTL, 'is-active', ACTION_UNIT+'.service', ACTION_UNIT+'.timer'],
                            capture_output=True, text=True, timeout=3, env=ENV, check=False)
    # A one-shot can be activating or running its ExecStopPost while
    # is-active --quiet returns false. Keep the existing busy guard then.
    return result.returncode == 0 or any(
        state in ('active', 'activating', 'deactivating', 'reloading', 'refreshing')
        for state in result.stdout.splitlines()
    )


def schedule(service, action):
    # Delayed one-shot lets PHP/Nginx finish sending the acknowledgement
    # before an operation stops/restarts the dashboard's own web services.
    if not isinstance(service, str) or not isinstance(action, str) or action not in ACTIONS:
        raise ValueError('Nieprawidlowa operacja uslugi.')
    rows = catalog()
    values = units_snapshot(rows)
    target = next((r for r in installed_rows(rows, values) if r['id'] == service), None)
    if target is None or values[target['unit']].get('LoadState') != 'loaded':
        raise ValueError('Usluga nie jest dostepna do sterowania.')
    if action_pending():
        raise RuntimeError('Trwa juz operacja uslugi. Odswiez stan.')
    receipt = uuid.uuid4().hex
    # PID 1 invokes the root-deployed helper after the actual systemctl
    # process exits (including failed starts and timeouts), before collecting
    # the transient unit. No user-supplied path, shell, extra resident process
    # or second service-control command is involved.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(RESULT_HELPER)):
        raise RuntimeError('Nieprawidlowa sciezka pomocnika uslug.')
    completion = f'/usr/bin/python3 -I {RESULT_HELPER} --record-service-result {service} {action} {receipt}'
    command = ['/usr/bin/systemd-run', '--quiet', '--collect', '--unit='+ACTION_UNIT,
               '--on-active=2s', '--timer-property=AccuracySec=100ms', '--timer-property=RemainAfterElapse=no',
               '--property=Type=oneshot', '--property=TimeoutStartSec=90s', '--property=TimeoutStopSec=15s',
               '--property=UMask=0077', '--property=ExecStopPost='+completion,
               SYSTEMCTL, '--no-ask-password', action, target['unit']]
    result = subprocess.run(command, capture_output=True, timeout=5, env=ENV, check=False)
    if result.returncode:
        raise RuntimeError('Nie udalo sie zlecic operacji. Sprawdz stan uslugi.')
    return {'accepted': True, 'service': service, 'action': action, 'receipt': receipt}


def record_completion(service, action, receipt, env):
    """Record a bounded, private receipt; never infer success from queueing."""
    if not isinstance(receipt, str) or not re.fullmatch(r'[a-f0-9]{32}', receipt) or action not in ACTIONS:
        raise ValueError('Invalid service result identity')
    target = next((r for r in catalog() if r['id'] == service), None)
    if target is None:
        raise ValueError('Invalid service result target')
    state = 'unknown'
    try:
        values = units_snapshot([target])[target['unit']]
        candidate = values.get('ActiveState')
        if values.get('LoadState') == 'loaded' and candidate in (
                'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'refreshing'):
            state = candidate
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass
    command_ok = (env.get('SERVICE_RESULT'), env.get('EXIT_CODE'), env.get('EXIT_STATUS')) == ('success', 'exited', '0')
    expected = 'inactive' if action == 'stop' else 'active'
    outcome = 'success' if command_ok and state == expected else 'error'
    stamp = int(time.time())
    # The existing /var/lib/devstack directory is root-only. Do not expose
    # raw systemctl output, unit command lines, or credentials to the web user.
    fd = os.open(RESULTS_DB, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(RESULTS_DB, timeout=3)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS completions (id TEXT PRIMARY KEY, service TEXT NOT NULL, name TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL, state TEXT NOT NULL, stamp INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO completions VALUES(?,?,?,?,?,?,?)',
                   (receipt, service, target['name'], action, outcome, state, stamp))
        db.execute('DELETE FROM completions WHERE stamp<?', (stamp-RESULT_TTL,))
        db.execute('DELETE FROM completions WHERE id IN (SELECT id FROM completions ORDER BY stamp DESC,id DESC LIMIT -1 OFFSET ?)', (RESULT_LIMIT,))


def completion_results():
    if not RESULTS_DB.exists():
        return []
    # The daemon only reads the fixed root-owned database; the authenticated
    # collector imports a sanitized snapshot, not the file itself.
    with closing(sqlite3.connect(RESULTS_DB.as_uri()+'?mode=ro', uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            'SELECT id,service,name,action,outcome,state,stamp FROM completions WHERE stamp>=? ORDER BY stamp,id LIMIT ?',
            (int(time.time())-RESULT_TTL, RESULT_LIMIT))]


if __name__ == '__main__':
    if os.geteuid() != 0 or len(sys.argv) != 5 or sys.argv[1] != '--record-service-result':
        raise SystemExit('Run only as root for the scheduled service result.')
    os.umask(0o077)
    record_completion(*sys.argv[2:], env=os.environ)
