"""Exercise the published extension through production HTTPS on a disposable runner."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from playwright.sync_api import expect, sync_playwright

ORIGIN = 'https://devstack.test'
EXTENSION = '/#/extensions/devstack-tools'
TOOLS = Path('/opt/devstack-tools')
SRC = Path(__file__).resolve().parents[1] / 'extensions/devstack-tools'
PROJECTS = Path.home() / 'projects'
PLAYWRIGHT = 'devstack-playwright'
MAIL = 'devstack-mail'
PLAIN = 'e2-plain-fixture'
LOCAL = 'e2-mail-fixture'
CONTEXT = 'e2-context-fixture'


def command(*args, check=True):
    return subprocess.run(args, check=check, timeout=200)


@contextmanager
def staged_release():
    stage = subprocess.check_output(['mktemp', '-d', '/tmp/devstack-tools.XXXXXXXX'], text=True).strip()
    try:
        payload = Path(stage) / 'payload'
        shutil.copytree(TOOLS / 'current', payload, symlinks=True)
        command('chmod', '-R', 'u+rwX', str(payload))
        publisher = ('sudo', '/bin/bash', str(SRC / 'publish.sh'), 'publish', stage, str(Path(shutil.which('node')).resolve()))
        yield payload, publisher
    finally:
        shutil.rmtree(stage)


@contextmanager
def native_notification_fixture():
    """Use the real opt-in path and an isolated launcher that only records payloads."""
    marker = Path('/etc/devstack/windows-local-notifications')
    mount = Path('/mnt/c')
    launcher = mount / 'DevStack/LocalNotifications/DevStack.Notifications.exe'
    output = launcher.with_name('ci-notification-events.jsonl')
    opt_in = ('sudo', '/bin/bash', str(SRC.parents[1] / 'scripts/enable-windows-local-notifications.sh'))
    status = subprocess.check_output([*opt_in, '--status'], text=True, timeout=10).strip()
    if status != 'disabled' or mount.exists() or mount.is_symlink():
        raise RuntimeError('Native notification fixture requires an unused marker and mount.')
    command('sudo', 'install', '-d', '-m', '0755', str(mount), str(mount / 'DevStack'), str(launcher.parent))
    try:
        script = r"""#!/usr/bin/python3
import base64
import json
from pathlib import Path
import sys
payload = json.loads(base64.b64decode(sys.argv[1]))
assert set(payload) == {'id', 'category', 'kind'}
with Path('ci-notification-events.jsonl').open('a') as f:
    f.write(json.dumps(payload) + '\n')
"""
        subprocess.run(['sudo', 'tee', str(launcher)], input=script, text=True, stdout=subprocess.DEVNULL, check=True)
        command('sudo', 'chmod', '0755', str(launcher))
        command('sudo', 'install', '-o', str(os.getuid()), '-m', '0600', '/dev/null', str(output))
        command(*opt_in)
        yield output
    finally:
        command('sudo', 'rm', '-f', str(marker), str(launcher), str(output))
        command('sudo', 'rmdir', str(launcher.parent), str(mount / 'DevStack'), str(mount))


def skills(slug):
    return PROJECTS / slug / '.agents/skills'


def wait_for(predicate, label, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.25)
    raise AssertionError(label)


def login(context):
    csrf = context.request.get(ORIGIN + '/api/session').json()['csrf']
    response = context.request.post(ORIGIN + '/api/login', data={'password': os.environ['DEVSTACK_E2E_PASSWORD']},
                                    headers={'X-CSRF-Token': csrf, 'Origin': ORIGIN})
    assert response.ok, response.status
    return {'X-CSRF-Token': response.json()['csrf'], 'Origin': ORIGIN}


def exercise(browser):
    context = browser.new_context(viewport={'width': 1370, 'height': 900}, locale='pl-PL')
    headers = login(context)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    context.route(re.compile(r'^https?://(?!devstack\.test(?:/|$))'), lambda route: route.abort())

    def post(path, data):
        response = context.request.post(ORIGIN + '/api/' + path, data=data, headers=headers)
        assert response.ok, response.text()
        return response.json()['result']

    def journal():
        code = 'require "/opt/devstack/notifications.php"; echo json_encode((new DevStackNotices())->poll("", false)["items"]);'
        wire = subprocess.check_output(['sudo', '-u', 'devstackweb', '/usr/bin/php', '-r', code], text=True, timeout=10)
        return json.loads(wire)

    def terminal(accept, title, kind, slug=None):
        receipt = accept['receipt']
        assert re.fullmatch('[a-f0-9]{32}', receipt) and accept['notification_id'] != 'op-' + receipt
        matched = []
        def observed():
            matched[:] = [item for item in journal() if item.get('receipt') == receipt]
            return any(item['id'] == 'op-' + receipt for item in matched)
        wait_for(observed, 'collector did not publish the instruction result', timeout=45)
        rows = {item['id']: item for item in matched}
        assert len(rows) == len(matched) == 2 and rows[accept['notification_id']]['kind'] == 'info', rows
        result = rows['op-' + receipt]
        assert result['title'] == title and result['kind'] == kind, rows
        expected_route = '/#/project/' + slug if slug else EXTENSION
        assert result['route'] == expected_route and rows[accept['notification_id']]['route'] == expected_route, rows
        if slug:
            assert result['description'].startswith(slug), rows
        return result

    def submit(click, expected=None):
        with page.expect_response(lambda response: response.request.method == 'POST' and
                                  response.url.endswith('/api/extensions/instructions')) as submitted:
            click()
        response = submitted.value
        assert response.ok, response.text()
        if expected is not None:
            assert response.request.post_data_json == expected
        return response.json()['result']

    def settle_php():
        # The ordinary installer and deletion API schedule PHP-FPM reloads
        # after the response. Let them finish before another mutation or UI check.
        def idle():
            output = subprocess.check_output([
                'systemctl', 'list-units', '--all', '--plain', '--no-legend', '--no-pager', 'devstack-fpm-reload-*',
            ], text=True, timeout=5)
            for line in output.splitlines():
                fields = line.split()
                if len(fields) >= 4 and fields[0].startswith('devstack-fpm-reload-') and (
                    fields[2] in {'activating', 'reloading', 'deactivating'} or fields[3] in {'waiting', 'running'}
                ):
                    return False
            return context.request.get(ORIGIN + '/api/session', timeout=3000).ok
        wait_for(idle, 'PHP-FPM did not settle after the scheduled reload')

    def register(slug, mail):
        post('projects', dict(slug=slug, name=slug, domain=slug + '.test', webroot='public', database=False,
                              https=False, local_mail=mail, engine='none', starter_kit='none', installer_options={}))
        settle_php()

    def delete(slug):
        post('projects/delete', {'slug': slug, 'confirmation': slug, 'delete_database': False})
        settle_php()

    def detail(path=''):
        response = context.request.get(ORIGIN + '/api/extensions/devstack-tools' + path)
        assert response.ok, response.text()
        return response.json()['result']

    def project(slug):
        return next(row for row in detail('/projects')['projects'] if row['slug'] == slug)

    def refresh():
        page.get_by_role('button', name='Odśwież dane usług i projektów').click()

    def show_project(slug, ready=True):
        page.goto(ORIGIN + '/#/project/' + slug)
        expect(page.get_by_role('heading', name=slug, exact=True)).to_be_visible()
        card = page.get_by_role('region', name='DevStack Tools', exact=True)
        if ready:
            expect(card).to_be_visible(timeout=15000)
        return card

    menu = page.get_by_role('navigation', name='Rozszerzenia')
    section_menu = page.get_by_role('navigation', name='Sekcje rozszerzenia')

    assert detail()['state'] == 'absent', detail()
    page.goto(ORIGIN + EXTENSION)
    expect(page.get_by_text('Rozszerzenie nie jest zainstalowane.')).to_be_visible()
    expect(menu).to_have_count(0)
    assert context.request.post(ORIGIN + '/api/extensions/instructions', data={'action': 'sync'}).status == 403
    with browser.new_context() as unauthenticated:
        assert unauthenticated.request.get(ORIGIN + '/api/extensions').status == 401

    register(PLAIN, False)
    plain = skills(PLAIN)
    plain.mkdir(parents=True)
    personal = plain / 'my-skill'
    personal.write_text('fixture from developer')
    legacy = plain / 'devstack-tools'
    legacy.symlink_to(TOOLS / 'skills/devstack-tools')
    command('bash', str(SRC / 'install.sh'))
    installed = detail()
    if installed['state'] != 'active':
        command('sudo', 'stat', '-c', '%n %F uid=%u mode=%a', '/opt', str(TOOLS),
                *(str(TOOLS / name) for name in ('.managed', '.lock', 'current', 'skills', 'releases')))
        probe = ('/usr/bin/python3', '-IB', '-c',
                 "import sys; sys.path.insert(0, '/opt/devstack/daemon'); "
                 "from extension_manifest import REGISTRY, discover_one; "
                 "print(discover_one('devstack-tools', *REGISTRY['devstack-tools'])['state'])")
        command('sudo', *probe, check=False)
        pid = subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', 'devstackd'], text=True).strip()
        command('sudo', 'nsenter', '--mount', '--target', pid, *probe, check=False)
    assert installed['state'] == 'active', installed
    refresh()
    expect(menu.get_by_role('button', name='Tools')).to_be_visible(timeout=20000)
    expect(page.get_by_role('heading', name='DevStack Tools', exact=True)).to_be_visible(timeout=20000)
    for tool in ('Playwright Test', 'Playwright CLI', 'DevStack Mail'):
        expect(page.get_by_role('heading', name=re.compile(tool))).to_be_visible()
    first_build = detail()['build']
    assert detail()['version'] == '0.3.1'
    assert all(tool['installed'] and tool['ready'] for tool in detail()['tools'])
    results = Path('browser-results')
    results.mkdir(exist_ok=True)
    page.screenshot(path=results / 'extensions-overview.png', full_page=True)
    legacy_inode = legacy.lstat().st_ino
    wait_for(lambda: project(PLAIN)['migratable'], 'manual skill missing')
    assert legacy.lstat().st_ino == legacy_inode and not (plain / PLAYWRIGHT).is_symlink()

    register(LOCAL, True)
    local = skills(LOCAL)
    wait_for(lambda: (local / MAIL).is_symlink() and (local / PLAYWRIGHT).is_symlink(), 'new project skills missing')
    original = project(LOCAL)['id']
    assert MAIL not in project(PLAIN)['wanted'] and MAIL in project(LOCAL)['wanted']
    page.get_by_role('navigation', name='Nawigacja główna').get_by_role('button', name='Projekty').click()
    page.go_back(wait_until='commit')
    expect(page.get_by_role('heading', name='DevStack Tools', exact=True)).to_be_visible()
    page.go_forward(wait_until='commit')
    expect(page.get_by_role('heading', name='Projekty', exact=True)).to_be_visible()
    menu.get_by_role('button', name='Tools').click()
    page.reload()
    expect(page.get_by_role('heading', name='DevStack Tools', exact=True)).to_be_visible()

    section_menu.get_by_role('button', name='Instrukcje').focus()
    page.keyboard.press('Enter')
    page.get_by_role('button', name='Podgląd instrukcji').first.click()
    expect(page.locator('pre')).to_contain_text('name: devstack-playwright')
    catalogue = page.get_by_role('region', name='Instrukcje')
    expect(catalogue.get_by_text(re.compile(r'Projekty \(1\).*' + LOCAL)).first).to_be_visible()

    section_menu.get_by_role('button', name='Projekty').click()
    plain_card = page.locator('[data-slot=card]').filter(has=page.get_by_role('link', name=PLAIN, exact=True))
    mail_card = page.locator('[data-slot=card]').filter(has=page.get_by_role('link', name=LOCAL, exact=True))
    expect(plain_card.get_by_text('Podłączono ręcznie', exact=True)).to_be_visible()
    page.screenshot(path=results / 'extensions-projects.png', full_page=True)
    page.once('dialog', lambda prompt: prompt.accept())
    migrated_global_view = submit(plain_card.get_by_role('button', name='Migruj ręczne dowiązanie').click)
    expect(page.get_by_text('Przyjęto synchronizację. Wynik pojawi się przy projektach.')).to_be_visible()
    wait_for(lambda: not legacy.is_symlink() and (plain / PLAYWRIGHT).is_symlink(), 'migration did not complete')
    terminal(migrated_global_view, 'Migracja instrukcji zakończona', 'success', PLAIN)
    assert personal.read_text() == 'fixture from developer'
    project_mode = mail_card.get_by_role('switch')
    expect(project_mode).to_be_checked()
    project_mode.click()
    expect(project_mode).not_to_be_checked(timeout=15000)
    wait_for(lambda: not (local / MAIL).is_symlink(), 'project disable did not detach managed link')
    project_mode.click()
    expect(project_mode).to_be_checked(timeout=15000)
    wait_for(lambda: (local / MAIL).is_symlink(), 'project re-enable did not attach skill')
    mail_inode = (local / MAIL).lstat().st_ino
    global_sync = submit(page.get_by_role('button', name='Synchronizuj wszystkie projekty').click)
    wait_for(lambda: project(LOCAL)['event'] == 'manual', 'manual retry did not complete')
    assert terminal(global_sync, 'Synchronizacja instrukcji zakończona', 'success')['description'] == 'Gotowe 2'
    assert mail_inode == (local / MAIL).lstat().st_ino
    mail_card.get_by_role('link', name=LOCAL, exact=True).click()
    expect(page.get_by_role('region', name='DevStack Tools')).to_be_visible()
    page.go_back(wait_until='commit')
    section_menu.get_by_role('button', name='Automatyzacje').click()
    expect(page.get_by_role('heading', name=re.compile('Istniejące projekty'))).to_be_visible()
    expect(page.get_by_role('switch', name='Automatyczne zarządzanie instrukcjami')).to_be_checked()

    contextual = skills(CONTEXT)
    contextual.mkdir(parents=True)
    contextual_legacy = contextual / 'devstack-tools'
    contextual_legacy.symlink_to(TOOLS / 'skills/devstack-tools')
    register(CONTEXT, False)
    # This fixture was registered via the API outside the browser.
    with page.expect_response(lambda response: response.url.endswith('/api/state') and response.ok):
        refresh()
    card = show_project(CONTEXT)
    legacy_button = card.get_by_role('button', name=re.compile('starsza instrukcja.*Podłączono ręcznie'))
    expect(legacy_button).to_be_visible()
    legacy_button.click()
    legacy_preview = page.get_by_role('dialog', name=re.compile('starsza instrukcja'))
    expect(legacy_preview.locator('pre')).to_contain_text('name: devstack-tools')
    page.keyboard.press('Escape')
    expect(legacy_preview).to_have_count(0)
    expect(legacy_button).to_be_focused()
    expect(page).to_have_url(ORIGIN + '/#/project/' + CONTEXT)
    migration = card.get_by_role('button', name='Migruj ręczne dowiązanie')
    page.once('dialog', lambda prompt: prompt.dismiss())
    migration.click()
    assert contextual_legacy.is_symlink()
    page.once('dialog', lambda prompt: prompt.accept())
    migrated = submit(migration.click)
    wait_for(lambda: not contextual_legacy.is_symlink() and (contextual / PLAYWRIGHT).is_symlink(),
             'contextual migration did not finish')
    expect(migration).to_have_count(0)
    terminal(migrated, 'Migracja instrukcji zakończona', 'success', CONTEXT)
    expect(page).to_have_url(ORIGIN + '/#/project/' + CONTEXT)

    card = show_project(LOCAL)
    mail_button = card.get_by_role('button', name=re.compile('DevStack Mail.*Zarządzane'))
    expect(mail_button).to_be_visible()
    expect(card.get_by_text('Ostatnia synchronizacja', exact=False)).not_to_be_visible()
    expect(card.get_by_text(re.compile(r'BUILD|Dokumentacja|1\.65\.1'))).to_have_count(0)
    page.screenshot(path=results / 'extensions-project-card.png', full_page=True)
    card.get_by_role('button', name='Szczegóły instrukcji').click()
    expect(card.get_by_text('Ostatnia synchronizacja', exact=False)).to_be_visible()
    expect(card.get_by_text('Włączona lokalna poczta', exact=False)).to_be_visible()
    mail_button.click()
    preview = page.get_by_role('dialog', name='DevStack Mail', exact=True)
    expect(preview.locator('pre')).to_contain_text('name: devstack-mail')
    preview.get_by_role('button', name='Zamknij podgląd').click()
    expect(page).to_have_url(ORIGIN + '/#/project/' + LOCAL)

    local_switch = card.get_by_role('switch')
    off = submit(local_switch.click)
    expect(local_switch).not_to_be_checked(timeout=15000)
    wait_for(lambda: not (local / MAIL).is_symlink(), 'contextual disable did not detach the instruction')
    assert project(LOCAL)['automatic'] is False
    off_result = terminal(off, 'Automatyczne instrukcje wyłączone', 'success', LOCAL)
    expect(page.locator('[data-slot="toast"]').get_by_text(off_result['title'], exact=True)).to_be_visible(timeout=15000)
    on = submit(local_switch.click)
    expect(local_switch).to_be_checked(timeout=15000)
    wait_for(lambda: (local / MAIL).is_symlink(), 'contextual enable did not attach the instruction')
    mail_inode = (local / MAIL).lstat().st_ino
    sync_result = submit(card.get_by_role('button', name='Synchronizuj instrukcje').click,
                         {'extension': 'devstack-tools', 'action': 'sync', 'project': original, 'card': 'tools'})
    terminal(on, 'Automatyczne instrukcje włączone', 'success', LOCAL)
    terminal(sync_result, 'Synchronizacja instrukcji zakończona', 'success', LOCAL)
    plain_project = show_project(PLAIN)
    expect(plain_project.get_by_role('button', name=re.compile('DevStack Mail'))).to_have_count(0)
    expect(plain_project.get_by_role('button', name=re.compile('DevStack Playwright.*Zarządzane'))).to_be_visible()

    # A replaced parent directory is rejected by the real worker. Retry
    # through the card after restoring the fixture's own directory.
    unsafe = PROJECTS / CONTEXT / '.agents'
    saved = unsafe.with_name('.agents-fixture-backup')
    card = show_project(CONTEXT)
    unsafe.rename(saved)
    unsafe.symlink_to(plain.parent, target_is_directory=True)
    try:
        failed = submit(card.get_by_role('button', name='Synchronizuj instrukcje').click)
        expect(card.get_by_text('Nie można bezpiecznie otworzyć katalogu')).to_be_visible(timeout=15000)
        terminal(failed, 'Synchronizacja instrukcji zakończona z problemami', 'error', CONTEXT)
        expect(card.get_by_role('button', name='Ponów synchronizację')).to_be_visible()
    finally:
        unsafe.unlink()
        saved.rename(unsafe)
    card.get_by_role('button', name='Ponów synchronizację').click()
    expect(card.get_by_text('Nie można bezpiecznie otworzyć katalogu')).to_have_count(0, timeout=15000)

    page.goto(ORIGIN + EXTENSION)
    section_menu.get_by_role('button', name='Automatyzacje').click()
    global_mode = page.get_by_role('switch', name='Automatyczne zarządzanie instrukcjami')
    global_off = submit(global_mode.click)
    expect(global_mode).not_to_be_checked(timeout=15000)
    card = show_project(LOCAL)
    expect(card.get_by_text('Automatyzacja wyłączona dla rozszerzenia.')).to_be_visible()
    expect(card.get_by_role('switch')).to_be_checked()
    page.go_back(wait_until='commit')
    section_menu.get_by_role('button', name='Automatyzacje').click()
    assert 'Gotowe 3' in terminal(global_off, 'Automatyczne instrukcje wyłączone', 'success')['description']
    global_on = submit(global_mode.click)
    expect(global_mode).to_be_checked(timeout=15000)
    wait_for(lambda: (local / MAIL).is_symlink(), 'global re-enable did not restore local mail instruction')
    mail_inode = (local / MAIL).lstat().st_ino
    assert 'Gotowe 3' in terminal(global_on, 'Automatyczne instrukcje włączone', 'success')['description']

    command('sudo', 'systemctl', 'stop', 'devstack-mailpit')
    try:
        wait_for(lambda: detail()['tools'][-1]['service_state'] == 'inactive', 'Mailpit status not updated')
        assert (local / MAIL).lstat().st_ino == mail_inode
        assert MAIL in project(LOCAL)['wanted']
        stopped_card = show_project(LOCAL)
        expect(stopped_card.get_by_role('button', name=re.compile('DevStack Mail.*Zarządzane'))).to_be_visible()
    finally:
        command('sudo', 'systemctl', 'start', 'devstack-mailpit')

    replacement = plain / PLAYWRIGHT
    replacement.unlink()
    replacement.write_text('custom instruction file')
    conflict = post('extensions/instructions', {'extension': 'devstack-tools', 'action': 'sync', 'project': project(PLAIN)['id']})
    wait_for(lambda: project(PLAIN)['status'] == 'conflict', 'user content collision was not reported')
    terminal(conflict, 'Konflikt synchronizacji instrukcji', 'error', PLAIN)
    assert replacement.read_text() == 'custom instruction file'
    conflict_card = show_project(PLAIN)
    expect(conflict_card.get_by_text('Konflikty · devstack-playwright')).to_be_visible()
    expect(conflict_card.get_by_role('button', name=re.compile('DevStack Playwright.*Konflikt'))).to_be_visible()
    page.goto(ORIGIN + EXTENSION)

    with staged_release() as (payload, publisher):
        manifest = payload / 'manifest.json'
        initial = json.loads(manifest.read_text())
        initial['view']['sections'][0]['label'] = 'Ponowne wydanie'
        initial['core_contract'] = 2
        manifest.write_text(json.dumps(initial))
        assert command(*publisher, check=False).returncode != 0
        assert detail()['build'] == first_build
        initial['core_contract'] = 1
        manifest.write_text(json.dumps(initial))
        skill_md = payload / 'skills/devstack-mail/SKILL.md'
        skill_md.write_text(skill_md.read_text() + '\n<!-- E2 central update -->\n')
        sidecar = payload / 'project-cards.json'
        definitions = json.loads(sidecar.read_text())
        extra = {**definitions['cards'][0], 'id': 'mail-slot-fixture', 'title': 'Kontekst poczty',
                 'slot': 'project-mail', 'requires': ['local_mail']}
        extra['sections'] = [{**extra['sections'][0], 'operations': ['execute']}]
        definitions['cards'].append(extra)
        sidecar.write_text(json.dumps(definitions))
        assert command(*publisher, check=False).returncode != 0
        extra['sections'][0]['operations'] = []
        sidecar.write_text(json.dumps(definitions))
        second_build = hashlib.sha256(manifest.read_bytes() + skill_md.read_bytes() + sidecar.read_bytes()).hexdigest()
        (payload / 'BUILD').write_text(second_build + '\n')
        command(*publisher)
    expect(section_menu.get_by_role('button', name='Ponowne wydanie')).to_be_visible(timeout=20000)
    assert detail()['build'] == second_build
    assert 'E2 central update' in (local / MAIL / 'SKILL.md').read_text()
    assert (local / MAIL).lstat().st_ino == mail_inode
    show_project(LOCAL)
    expect(page.get_by_role('region', name='Kontekst poczty')).to_be_visible()
    show_project(PLAIN)
    expect(page.get_by_role('region', name='Kontekst poczty')).to_have_count(0)
    page.goto(ORIGIN + EXTENSION)
    command('bash', str(SRC / 'rollback.sh'))
    wait_for(lambda: detail()['build'] == first_build, 'rollback manifest not observed')
    assert 'E2 central update' not in (local / MAIL / 'SKILL.md').read_text()

    active_manifest = (TOOLS / 'current/manifest.json').resolve()
    original_manifest = active_manifest.read_bytes()
    try:
        for content, expected in [(b'{broken}', 'invalid'), (json.dumps({**json.loads(original_manifest), 'contract': 9}).encode(), 'incompatible')]:
            subprocess.run(['sudo', 'tee', str(active_manifest)], input=content, stdout=subprocess.DEVNULL, check=True)
            assert detail()['state'] == expected
            refresh()
            expect(menu).to_have_count(0)
            assert (local / MAIL).is_symlink()
    finally:
        subprocess.run(['sudo', 'tee', str(active_manifest)], input=original_manifest, stdout=subprocess.DEVNULL, check=True)
    refresh()
    expect(menu.get_by_role('button', name='Tools')).to_be_visible()

    command('sudo', 'systemctl', 'restart', 'devstackd')
    wait_for(lambda: context.request.get(ORIGIN + '/api/extensions', timeout=3000).ok,
             'Core API did not recover after restart')
    assert (local / MAIL).is_symlink(), 'managed instruction lost after Core restart'
    delete(LOCAL)
    wait_for(lambda: not (local / MAIL).is_symlink(), 'deleted project instructions not cleaned')
    assert (PROJECTS / LOCAL).exists()
    register(LOCAL, True)
    wait_for(lambda: (local / MAIL).is_symlink(), 'recreated project not synchronized')
    assert project(LOCAL)['id'] != original
    response = context.request.post(ORIGIN + '/api/extensions/instructions', headers=headers,
                                    data={'extension': 'devstack-tools', 'action': 'sync', 'project': original, 'card': 'tools'})
    assert not response.ok
    with staged_release() as (payload, publisher):
        (payload / 'project-cards.json').unlink()
        for part in ('manifest.json', 'playwright/package.json'):
            file = payload / part
            data = json.loads(file.read_text())
            data['version'] = '0.3.0'
            file.write_text(json.dumps(data))
        (payload / 'BUILD').write_text(hashlib.sha256((payload / 'manifest.json').read_bytes()).hexdigest() + '\n')
        command(*publisher)
    command('bash', str(SRC / 'rollback.sh'))
    show_project(LOCAL)
    command('bash', str(SRC / 'rollback.sh'))
    expect(page.get_by_role('region', name='DevStack Tools')).to_have_count(0)
    assert detail()['version'] == '0.3.0' and detail()['state'] == 'active'
    assert (local / MAIL).is_symlink()
    command('bash', str(SRC / 'rollback.sh'))
    expect(page.get_by_role('region', name='DevStack Tools')).to_be_visible(timeout=15000)

    # Reload and wait for a genuine automatic audit. Neither can re-emit a
    # previously collected receipt. Then close all panel pages for real WinRT collection.
    page.reload()
    checked_at = project(LOCAL)['last_sync']
    wait_for(lambda: project(LOCAL)['last_sync'] != checked_at, 'periodic instruction audit did not run', timeout=50)
    assert sum(item['id'] == off_result['id'] for item in journal()) == 1
    page.wait_for_function('navigator.serviceWorker.controller !== null')
    with page.expect_event('close'):
        page.close(run_before_unload=True)

    def no_present_clients():
        code = ('echo (new PDO("sqlite:/var/lib/devstack-web/notifications.sqlite3"))'
                '->query("SELECT COUNT(*) FROM clients WHERE expires > " . time())->fetchColumn();')
        return subprocess.check_output(['sudo', '-u', 'devstackweb', '/usr/bin/php', '-r', code],
                                       text=True, timeout=10).strip() == '0'
    # Allow the ordinary 90-second lease to expire if teardown loses a beacon.
    wait_for(no_present_clients, 'closed panel still has a notification lease', timeout=95)
    with native_notification_fixture() as output:
        closed = post('extensions/instructions', {'extension': 'devstack-tools', 'action': 'sync', 'project': project(LOCAL)['id']})
        payload = {'id': 'op-' + closed['receipt'], 'category': 'project', 'kind': 'success'}
        def delivered():
            return payload in [json.loads(line) for line in output.read_text().splitlines()]
        wait_for(delivered, 'collector failed to deliver a closed-panel result through the native launcher', timeout=45)
        assert [json.loads(line) for line in output.read_text().splitlines()].count(payload) == 1
        terminal(closed, 'Synchronizacja instrukcji zakończona', 'success', LOCAL)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    show_project(LOCAL)

    page.get_by_role('button', name='Włącz ciemny motyw').click()
    expect(page.locator('html')).to_have_class(re.compile('dark'))
    with browser.new_context(viewport={'width': 390, 'height': 820}, is_mobile=True, has_touch=True,
                             device_scale_factor=2, locale='pl-PL') as mobile:
        login(mobile)
        mobile_page = mobile.new_page()
        mobile_page.goto(ORIGIN + '/#/projects')
        mobile_page.get_by_role('button', name='Otwórz menu').tap()
        mobile_page.get_by_role('button', name='Tools').tap()
        expect(mobile_page.get_by_role('heading', name='DevStack Tools', exact=True)).to_be_visible()
        expect(mobile_page.locator('html')).to_have_class(re.compile('dark'))
        assert mobile_page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        mobile_page.screenshot(path=results / 'extensions-mobile-dark.png', full_page=True)
        mobile_page.goto(ORIGIN + '/#/project/' + LOCAL)
        mobile_card = mobile_page.get_by_role('region', name='DevStack Tools')
        mobile_mail = mobile_card.get_by_role('button', name=re.compile('DevStack Mail'))
        expect(mobile_mail).to_be_visible()
        assert mobile_page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        mobile_page.screenshot(path=results / 'extensions-project-card-mobile.png', full_page=True)
        mobile_mail.tap()
        expect(mobile_page.get_by_role('dialog', name='DevStack Mail')).to_be_visible()
        mobile_page.get_by_role('button', name='Zamknij podgląd').tap()
        mobile_page.get_by_role('button', name='Włącz jasny motyw').tap()
        expect(mobile_page.locator('html')).not_to_have_class(re.compile('dark'))

    command('bash', str(SRC / 'uninstall.sh'), '--confirm')
    expect(page.get_by_role('region', name='DevStack Tools')).to_have_count(0)
    page.goto(ORIGIN + EXTENSION)
    refresh()
    expect(page.get_by_role('navigation', name='Rozszerzenia')).to_have_count(0)
    expect(page.get_by_text('Rozszerzenie nie jest zainstalowane.')).to_be_visible(timeout=20000)
    wait_for(lambda: not (local / MAIL).is_symlink(), 'uninstall did not detach managed instructions')
    assert personal.read_text() == 'fixture from developer'
    assert replacement.read_text() == 'custom instruction file'
    command('systemctl', 'is-active', '--quiet', 'devstack-mailpit')
    delete(LOCAL)
    delete(PLAIN)
    delete(CONTEXT)
    assert not errors, errors
    assert (PROJECTS / LOCAL).exists()
    page.screenshot(path=results / 'extensions-uninstalled.png', full_page=True)
    context.close()


if __name__ == '__main__':
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('DEVSTACK_E2E_ISOLATED') != '1'
            or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()):
        raise SystemExit('Only run against a disposable DevStack CI installation.')
    if TOOLS.exists() or any((PROJECTS / slug).exists() for slug in (LOCAL, PLAIN, CONTEXT)):
        raise SystemExit('Expected a clean extension and project fixture.')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        exercise(browser)
        browser.close()
    print('PASS: installed extension, dynamic UI, durable Agent Skills and terminal lifecycle')
