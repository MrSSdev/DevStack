"""Exercise the published extension through production HTTPS on a disposable runner."""

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


def command(*args, check=True):
    return subprocess.run(args, check=check, timeout=200)


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

    def register(slug, mail):
        post('projects', dict(slug=slug, name=slug, domain=slug + '.test', webroot='public', database=False,
                              https=False, local_mail=mail, engine='none', starter_kit='none', installer_options={}))

    def delete(slug):
        post('projects/delete', {'slug': slug, 'confirmation': slug, 'delete_database': False})

    def detail(path=''):
        response = context.request.get(ORIGIN + '/api/extensions/devstack-tools' + path)
        assert response.ok, response.text()
        return response.json()['result']

    def project(slug):
        return next(row for row in detail('/projects')['projects'] if row['slug'] == slug)

    def refresh():
        page.get_by_role('button', name='Odśwież dane usług i projektów').click()

    menu = page.get_by_role('navigation', name='Rozszerzenia')
    section_menu = page.get_by_role('navigation', name='Sekcje rozszerzenia')

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
    refresh()
    expect(menu.get_by_role('button', name='Tools')).to_be_visible(timeout=20000)
    expect(page.get_by_role('heading', name='DevStack Tools', exact=True)).to_be_visible(timeout=20000)
    for tool in ('Playwright Test', 'Playwright CLI', 'DevStack Mail'):
        expect(page.get_by_role('heading', name=re.compile(tool))).to_be_visible()
    first_build = detail()['build']
    assert detail()['version'] == '0.3.0'
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
    expect(page.get_by_text(LOCAL, exact=False).first).to_be_visible()

    section_menu.get_by_role('button', name='Projekty').click()
    plain_card = page.locator('[data-slot=card]').filter(has=page.get_by_role('link', name=PLAIN, exact=True))
    mail_card = page.locator('[data-slot=card]').filter(has=page.get_by_role('link', name=LOCAL, exact=True))
    expect(plain_card.get_by_text('Podłączono ręcznie', exact=True)).to_be_visible()
    page.once('dialog', lambda prompt: prompt.accept())
    plain_card.get_by_role('button', name='Migruj ręczne dowiązanie').click()
    expect(page.get_by_text('Przyjęto synchronizację. Wynik pojawi się przy projektach.')).to_be_visible()
    wait_for(lambda: not legacy.is_symlink() and (plain / PLAYWRIGHT).is_symlink(), 'migration did not complete')
    assert personal.read_text() == 'fixture from developer'
    mail_card.get_by_role('switch').click()
    wait_for(lambda: not (local / MAIL).is_symlink(), 'project disable did not detach managed link')
    mail_card.get_by_role('switch').click()
    wait_for(lambda: (local / MAIL).is_symlink(), 'project re-enable did not attach skill')
    mail_inode = (local / MAIL).lstat().st_ino
    page.get_by_role('button', name='Synchronizuj wszystkie projekty').click()
    wait_for(lambda: project(LOCAL)['event'] == 'manual', 'manual retry did not complete')
    assert mail_inode == (local / MAIL).lstat().st_ino
    mail_card.get_by_role('link', name=LOCAL, exact=True).click()
    expect(page.get_by_role('heading', name='Zarządzane instrukcje')).to_be_visible()
    page.get_by_role('link', name='DevStack Tools').click()
    section_menu.get_by_role('button', name='Automatyzacje').click()
    expect(page.get_by_role('heading', name=re.compile('Istniejące projekty'))).to_be_visible()
    expect(page.get_by_role('switch', name='Automatyczne zarządzanie instrukcjami')).to_be_checked()

    command('sudo', 'systemctl', 'stop', 'devstack-mailpit')
    try:
        wait_for(lambda: detail()['tools'][-1]['service_state'] == 'inactive', 'Mailpit status not updated')
        assert (local / MAIL).lstat().st_ino == mail_inode
        assert MAIL in project(LOCAL)['wanted']
    finally:
        command('sudo', 'systemctl', 'start', 'devstack-mailpit')

    replacement = plain / PLAYWRIGHT
    replacement.unlink()
    replacement.write_text('custom instruction file')
    post('extensions/instructions', {'extension': 'devstack-tools', 'action': 'sync', 'project': project(PLAIN)['id']})
    wait_for(lambda: project(PLAIN)['status'] == 'conflict', 'user content collision was not reported')
    assert replacement.read_text() == 'custom instruction file'

    stage = subprocess.check_output(['mktemp', '-d', '/tmp/devstack-tools.XXXXXXXX'], text=True).strip()
    try:
        payload = Path(stage) / 'payload'
        shutil.copytree(TOOLS / 'current', payload, symlinks=True)
        command('chmod', '-R', 'u+rwX', str(payload))
        manifest = payload / 'manifest.json'
        initial = json.loads(manifest.read_text())
        initial['view']['sections'][0]['label'] = 'Ponowne wydanie'
        initial['core_contract'] = 2
        manifest.write_text(json.dumps(initial))
        publisher = ('sudo', '/bin/bash', str(SRC / 'publish.sh'), 'publish', stage, str(Path(shutil.which('node')).resolve()))
        assert command(*publisher, check=False).returncode != 0
        assert detail()['build'] == first_build
        initial['core_contract'] = 1
        manifest.write_text(json.dumps(initial))
        skill_md = payload / 'skills/devstack-mail/SKILL.md'
        skill_md.write_text(skill_md.read_text() + '\n<!-- E2 central update -->\n')
        second_build = hashlib.sha256(manifest.read_bytes() + skill_md.read_bytes()).hexdigest()
        (payload / 'BUILD').write_text(second_build + '\n')
        command(*publisher)
    finally:
        shutil.rmtree(stage)
    expect(section_menu.get_by_role('button', name='Ponowne wydanie')).to_be_visible(timeout=20000)
    assert detail()['build'] == second_build
    assert 'E2 central update' in (local / MAIL / 'SKILL.md').read_text()
    assert (local / MAIL).lstat().st_ino == mail_inode
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
    wait_for(lambda: (local / MAIL).is_symlink(), 'managed instruction lost after Core restart')
    delete(LOCAL)
    wait_for(lambda: not (local / MAIL).is_symlink(), 'deleted project instructions not cleaned')
    assert (PROJECTS / LOCAL).exists()
    register(LOCAL, True)
    wait_for(lambda: (local / MAIL).is_symlink(), 'recreated project not synchronized')
    assert project(LOCAL)['id'] != original

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
        mobile_page.get_by_role('button', name='Włącz jasny motyw').tap()
        expect(mobile_page.locator('html')).not_to_have_class(re.compile('dark'))

    command('bash', str(SRC / 'uninstall.sh'), '--confirm')
    refresh()
    expect(menu).to_have_count(0)
    expect(page.get_by_text('Rozszerzenie nie jest zainstalowane.')).to_be_visible(timeout=20000)
    wait_for(lambda: not (local / MAIL).is_symlink(), 'uninstall did not detach managed instructions')
    assert personal.read_text() == 'fixture from developer'
    assert replacement.read_text() == 'custom instruction file'
    command('systemctl', 'is-active', '--quiet', 'devstack-mailpit')
    delete(LOCAL)
    delete(PLAIN)
    assert not errors, errors
    assert (PROJECTS / LOCAL).exists()
    results = Path('browser-results')
    results.mkdir(exist_ok=True)
    page.screenshot(path=results / 'extensions-desktop.png', full_page=True)
    context.close()


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('DEVSTACK_E2E_ISOLATED') != '1':
        raise SystemExit('Only run against a disposable DevStack CI installation.')
    if TOOLS.exists() or any((PROJECTS / slug).exists() for slug in (LOCAL, PLAIN)):
        raise SystemExit('Expected a clean extension and project fixture.')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        exercise(browser)
        browser.close()
    print('PASS: installed extension, dynamic UI, durable Agent Skills and terminal lifecycle')
