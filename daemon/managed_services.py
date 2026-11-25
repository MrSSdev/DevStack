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

CATALOG = Path(__file__).resolve().with_name('service-catalog.json')
SELECTION = Path('/var/lib/devstack-widget-selection.json')
SYSTEMCTL = '/usr/bin/systemctl'
ACTION_UNIT = 'devstack-service-control'
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


def units_snapshot(rows, metrics=False):
    props = 'Id,LoadState,ActiveState'
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


def public_rows():
    rows = catalog()
    values = units_snapshot(rows)
    installed = installed_rows(rows, values)
    selected = selection(rows)
    return [{**r, 'state': values[r['unit']].get('ActiveState', 'unknown')[:30],
             'load_state': values[r['unit']].get('LoadState', 'unknown')[:30],
             'widget_selected': r['id'] in selected} for r in installed]


def action_pending():
    result = subprocess.run([SYSTEMCTL, 'is-active', '--quiet', ACTION_UNIT+'.service', ACTION_UNIT+'.timer'],
                            capture_output=True, timeout=3, env=ENV, check=False)
    return result.returncode == 0


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
    command = ['/usr/bin/systemd-run', '--quiet', '--collect', '--unit='+ACTION_UNIT,
               '--on-active=2s', '--timer-property=AccuracySec=100ms', '--timer-property=RemainAfterElapse=no',
               '--property=TimeoutStartSec=90s', SYSTEMCTL, '--no-ask-password',
               action, target['unit']]
    result = subprocess.run(command, capture_output=True, timeout=5, env=ENV, check=False)
    if result.returncode:
        raise RuntimeError('Nie udalo sie zlecic operacji. Sprawdz stan uslugi.')
    return {'accepted': True, 'service': service, 'action': action}
