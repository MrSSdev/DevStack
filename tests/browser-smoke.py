"""Destructive acceptance tests for a disposable, fully installed DevStack.

The app, PHP session boundary, Unix helper, Nginx, databases, and Mailpit are
real. The dedicated notification lifecycle scenario replays only engine_job in a state response. The context guards every external HTTP(S) request; only the two controlled image fixtures are fulfilled during explicit image opt-in. Mailbox requests and writes are real. Never use a personal inbox.
"""
import base64
import json
import os
import pathlib
import re
import smtplib
import subprocess
import time
import unittest
import urllib.request
from email.message import EmailMessage

from playwright.sync_api import expect, sync_playwright

BASE = 'https://devstack.test'
OUT = pathlib.Path('browser-results')
HTML_SUBJECT = 'HTML fixture — Zażółć gęślą jaźń'
FIXTURE_IMAGES = {'https://example.invalid/tracker', 'https://example.invalid/css-pixel.png'}
FIXTURE_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6uoAAAAASUVORK5CYII=')
CSP_FAILURES = {'csp', 'net::ERR_BLOCKED_BY_CSP'}
HTML = '''<!doctype html><html lang="pl"><head><style>
body{background:rgb(248,250,252);color:rgb(17,24,39)}
.mail-card{width:560px;max-width:100%;margin:auto;background:white;padding:24px}
.mail-button{display:inline-block;background:rgb(22,22,22);color:white;padding:12px 20px;border-radius:5px;text-decoration:none}
.remote-probe{background-image:url(https://example.invalid/css-pixel.png);width:5px;height:5px}
@media(max-width:500px){.mail-card{padding:10px}}
</style><meta http-equiv="refresh" content="0;url=https://example.invalid/redirect"><base href="https://example.invalid/"></head>
<body><div class="remote-probe"></div><div class="mail-card"><h1>Potwierdzenie rezerwacji</h1><p>Cześć <strong>Łukasz</strong>! Zażółć gęślą jaźń.</p>
<table><tr><th>Usługa</th><th>Cena</th></tr><tr><td>Serwis</td><td>123,45 zł</td></tr></table>
<ul><li>Pierwszy punkt</li><li>Drugi punkt</li></ul><blockquote>Treść cytowana</blockquote>
<a class="mail-button" href="https://example.invalid/reset">Potwierdź</a>
<img src="https://example.invalid/tracker" alt="Remote" onerror="window.mailAttack=1">
<img src="cid:fixture-logo@test" alt="Fixture logo" width="24" height="24">
<script>window.mailAttack=1</script><iframe srcdoc="bad"></iframe>
<svg onload="window.mailAttack=1"></svg><form action="https://example.invalid"><input autofocus></form>
</div></body></html>'''


def mailpit(method='GET', path='/messages', data=None):
    req = urllib.request.Request('http://127.0.0.1:8025/api/v1' + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as response:
        body = response.read()
        return json.loads(body) if body[:1] in (b'{', b'[') else body


def send(subject, html=None):
    m = EmailMessage()
    m['From'] = 'Demo application <hello@browser-fixture.test>'
    m['To'] = 'Developer <recipient@example.test>'
    m['Subject'] = subject
    m.set_content('Plain fixture body — Zażółć gęślą jaźń')
    if html:
        m.add_alternative(html, subtype='html')
        m.get_payload()[-1].add_related(FIXTURE_PNG, maintype='image', subtype='png', cid='<fixture-logo@test>', filename='logo.png')
        m.add_attachment(b'Exact fixture bytes\n', maintype='text', subtype='plain', filename='fixture.txt')
    with smtplib.SMTP('127.0.0.1', 1025, timeout=5) as smtp:
        smtp.send_message(m)


class BrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('DEVSTACK_E2E_ISOLATED') != '1' or not os.environ.get('DEVSTACK_E2E_PASSWORD'):
            raise RuntimeError('Disposable installation and DEVSTACK_E2E_PASSWORD required; mailbox WILL be cleared.')
        OUT.mkdir(exist_ok=True)
        cls.pw = sync_playwright().start()
        kwargs = {'executable_path': os.environ['DEVSTACK_CHROMIUM']} if os.environ.get('DEVSTACK_CHROMIUM') else {}
        cls.browser = cls.pw.chromium.launch(**kwargs)
        cls.screens = []

    @classmethod
    def tearDownClass(cls):
        (OUT / 'screens.json').write_text(json.dumps(cls.screens, ensure_ascii=False, indent=2))
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        # Exercise the production network-only service worker in every
        # scenario. Playwright 1.60's service_workers='block' installs an init
        # script that reads navigator.serviceWorker in every frame. That getter
        # throws in our intentionally opaque, script-disabled mail iframe.
        # Do not inject that shim or weaken the iframe/CSP to accommodate it.
        # The worker handles only same-origin root navigations, not API calls
        # or images; the independent context network guard stays in force.
        self.context = self.browser.new_context(viewport={'width':853, 'height':950}, locale='pl-PL', service_workers='allow')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.external = []
        self.unexpected_network = []
        self.fixture_image_requests = []
        self.external_images_enabled = False
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        # Chromium can emit a request event for a CSS url() even when CSP
        # blocks it before the network. Keep Request objects to examine their
        # final failure, not merely the URL in the initial event.
        self.context.on('request', lambda req: self.external.append(req) if not req.url.startswith((BASE + '/', 'data:')) else None)
        # Independent network guard. Nothing external is allowed to leave
        # this browser context, even if the application's CSP regresses.
        # Context routing also covers requests from sandboxed child frames.
        self.context.route(re.compile(r'^https?://(?!devstack\.test(?:/|$))', re.I), self.guard_external_request)
        self.csrf = self.context.request.get(BASE + '/api/session').json()['csrf']
        login = self.post('/api/login', {'password':os.environ['DEVSTACK_E2E_PASSWORD']})
        self.assertEqual(login.status, 200, login.text())
        self.csrf = login.json()['csrf']
        self.context.tracing.start(screenshots=True, snapshots=True)
        self.addCleanup(lambda: self.context.tracing.stop(path=str(OUT / (self._testMethodName+'.zip'))))
        mailpit('DELETE', data={'IDs':[]})
        # Wait for Mailpit's search index, not a fixed delay.
        self.wait_total(0)

    def tearDown(self):
        self.page.screenshot(path=str(OUT / (self._testMethodName+'-last.png')), full_page=True)
        self.assertEqual(self.errors, [], 'Browser runtime errors')
        self.assert_mail_network()

    def guard_external_request(self, route):
        req = route.request
        if self.external_images_enabled and req.resource_type == 'image' and req.url in FIXTURE_IMAGES:
            self.fixture_image_requests.append(req)
            route.fulfill(status=200, content_type='image/png', body=FIXTURE_PNG)
        else:
            self.unexpected_network.append((req.url, req.resource_type))
            route.abort()

    def assert_mail_network(self):
        self.assertEqual(self.unexpected_network, [], 'Unexpected request reached the network interception boundary')
        # Allow only a browser-confirmed CSP rejection, or a specific image
        # request fulfilled by the fixture guard after explicit opt-in.
        # DNS failures, generic aborts, pending requests and successes that
        # bypassed the guard are not evidence of CSP protection.
        unexpected = [(req.url, req.failure) for req in self.external
                      if req not in self.fixture_image_requests and req.failure not in CSP_FAILURES]
        self.assertEqual(unexpected, [], 'External requests not proven blocked by CSP or explicitly fulfilled')

    def post(self, path, data, **kwargs):
        return self.context.request.post(BASE + path, data=data,
            headers={'X-CSRF-Token':self.csrf, 'Origin':BASE}, **kwargs)

    def wait_total(self, count):
        for _ in range(100):
            if mailpit()['total'] == count:return
            time.sleep(.1)
        self.fail(f'Mailpit did not reach {count} messages')

    def goto(self, route):
        target=BASE + '/#/' + route
        # An in-memory project detail deliberately retains its source hash.
        # Navigating to that same URL must reload to exercise a direct entry.
        if self.page.url==target:
            self.page.reload()
        else:
            self.page.goto(target)
        expect(self.page.locator('main')).to_be_visible()

    def theme(self, theme):
        dark = self.page.locator('html').evaluate("e=>e.classList.contains('dark')")
        if dark != (theme=='dark'):
            self.page.get_by_role('button', name='Włącz jasny motyw' if dark else 'Włącz ciemny motyw').click()
        if theme=='dark':
            expect(self.page.locator('html')).to_have_class(re.compile(r'\bdark\b'))
        else:
            expect(self.page.locator('html')).not_to_have_class(re.compile(r'\bdark\b'))
        # Exclude ending toasts; nth locators shift as each toast is removed.
        close = self.page.locator('[data-slot="toast"]:not([data-ending-style]) button[aria-label="Zamknij powiadomienie"]')
        while close.count():
            close.first.click()

    def shot(self, name):
        self.page.evaluate('document.fonts.ready')
        # Wait for the navigation transition before measuring the hidden drawer.
        self.page.wait_for_timeout(200)
        self.page.screenshot(path=str(OUT / (name+'.png')), full_page=True)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), self.page.evaluate('innerWidth'), name)
        self.screens.append(name)

    def message_button(self, subject):
        return self.page.locator('main button').filter(has_text=subject)

    def test_01_real_project_and_all_views(self):
        response = self.post('/api/projects', dict(slug='browser-fixture', name='Browser fixture', domain='browser-fixture.test', webroot='public', database=True, https=True, local_mail=True, engine='none', starter_kit='none', installer_options={}))
        self.assertEqual(response.status, 200, response.text())
        # Simulate an existing manually provisioned project with actual Composer
        # installed-metadata format. The installed daemon reads it as this user.
        metadata = pathlib.Path.home() / 'projects/browser-fixture/vendor/composer/installed.json'
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps({'packages':[{'name':'laravel/framework','version':'v13.42.0'}]}))
        # Verify the actual daemon RPC before exercising client-side search.
        state = self.context.request.get(BASE + '/api/state')
        self.assertEqual(state.status, 200, state.text())
        project = next(p for p in state.json()['result']['projects'] if p['slug']=='browser-fixture')
        self.assertEqual(project.get('detected_framework'), {'name':'Laravel','version':'13.42.0'},
                         'Installed daemon did not return the Composer version')
        self.goto('projects')
        search = self.page.get_by_role('searchbox',name='Szukaj projektów',exact=True)
        search.fill('laravel 13.42')
        row = self.page.locator('main button').filter(has_text='Browser fixture')
        expect(row.get_by_text('Laravel 13.42.0',exact=True)).to_be_visible()
        search.fill('does-not-exist.test')
        expect(self.page.get_by_text('Brak projektów pasujących do wyszukiwania.',exact=True)).to_be_visible()
        self.page.get_by_role('button',name='Wyczyść wyszukiwanie projektów',exact=True).click()
        expect(search).to_have_value('')
        search.fill('browser-fixture.test')
        expect(row).to_be_visible()
        search.fill('')
        metadata.write_text(json.dumps({'packages':[{'name':'laravel/framework','version':'v13.43.0'}]}))
        state = self.context.request.get(BASE + '/api/state')
        self.assertEqual(state.status, 200, state.text())
        project = next(p for p in state.json()['result']['projects'] if p['slug']=='browser-fixture')
        self.assertEqual(project.get('detected_framework'), {'name':'Laravel','version':'13.43.0'})
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(row.get_by_text('Laravel 13.43.0',exact=True)).to_be_visible()
        # Seed a project using the former two-field API, then open and save it
        # using the single editor. The real helper must retain both rule types.
        flux = (r'location ~* ^/flux/flux\.js$ {'
                ' expires off; try_files $uri $uri/ /index.php?$query_string; }')
        legacy = self.post('/api/config', {'slug':'browser-fixture','overrides':'client_max_body_size 64m;','extra_locations':flux})
        self.assertEqual(legacy.status, 200, legacy.text())
        self.goto('projects')
        self.page.locator('main button').filter(has_text='Browser fixture').click()
        editor = self.page.get_by_role('textbox',name='Dodatkowe reguły Nginx',exact=True)
        expect(editor).to_have_count(1)
        legacy_rules = legacy.json()['result']
        expect(editor).to_have_value(legacy_rules['overrides'] + '\n\n' + legacy_rules['extra_locations'])
        expect(self.page.get_by_text('Dodatkowe reguły — location /',exact=True)).to_have_count(0)
        expect(self.page.get_by_text('Dodatkowe lokalizacje zasobów JS/CSS',exact=True)).to_have_count(0)
        editor.fill('client_max_body_size 128m;\n\n' + flux)
        self.page.get_by_role('button',name='Sprawdź i zastosuj',exact=True).click()
        expect(self.page.locator('[data-slot="toast"]').filter(has_text='Konfiguracja przetestowana')).to_be_visible()
        expected_rules = 'client_max_body_size 128m;\n\n' + legacy_rules['extra_locations']
        expect(editor).to_have_value(expected_rules)
        invalid = self.post('/api/config', {'slug':'browser-fixture','rules':'include /etc/shadow;'})
        self.assertEqual(invalid.status,422)
        # Verify actual persisted state and the generated vhost, independent
        # of the React draft. An invalid edit must not change either region.
        persisted = self.context.request.get(BASE + '/api/state').json()['result']
        saved = next(p for p in persisted['projects'] if p['slug']=='browser-fixture')
        self.assertEqual(saved['overrides'], 'client_max_body_size 128m;')
        self.assertEqual(saved['extra_locations'], legacy_rules['extra_locations'])
        self.assertIn('client_max_body_size 128m;', saved['nginx_config'])
        self.assertIn('location ~* ^/flux/', saved['nginx_config'])
        self.goto('projects')
        self.page.locator('main button').filter(has_text='Browser fixture').click()
        expect(editor).to_have_value(expected_rules)
        long_slug='long-'+'a'*35
        response=self.post('/api/projects',dict(slug=long_slug,name='A'*70,domain=long_slug+'.test',webroot='public',database=False,https=False,local_mail=False,engine='none',starter_kit='none',installer_options={}))
        self.assertEqual(response.status,200,response.text())
        send(HTML_SUBJECT, HTML)
        self.wait_total(1)
        for width in [640, 853, 1024, 1280, 1920]:
            self.page.set_viewport_size({'width':width, 'height':950})
            for theme in ['light','dark']:
                self.goto('dashboard');self.theme(theme)
                header_origin = None
                for route in ['dashboard','projects','new','mail','settings']:
                    with self.subTest(width=width, theme=theme, route=route):
                        self.goto(route)
                        prefix=f'{width}-{theme}-'
                        if route=='mail':expect(self.message_button(HTML_SUBJECT)).to_be_visible()
                        if route=='new':self.page.get_by_role('combobox',name='Framework',exact=True).select_option('none')
                        self.shot(prefix+route)
                        # All primary views use the same title origin and
                        # page width, including on platforms with classic scrollbars.
                        header = self.page.locator('[data-slot="page-header"]')
                        expect(header).to_have_count(1)
                        expect(self.page.locator('main h1')).to_have_count(1)
                        origin = header.evaluate('e=>{const r=e.getBoundingClientRect();return [r.x,r.y+scrollY,r.width]}')
                        if header_origin is None:
                            header_origin = origin
                        else:
                            for expected, actual in zip(header_origin, origin):
                                self.assertAlmostEqual(expected, actual, delta=1, msg=prefix+route)
                        self.assertTrue(self.page.locator('main footer').evaluate("""e=>{const padding=parseFloat(getComputedStyle(e.closest('main')).paddingBottom);return e.getBoundingClientRect().bottom+scrollY+padding>=innerHeight-2}"""), prefix+route+' footer')
                        if route=='mail':
                            expect(header.get_by_role('button',name='Konfiguracja poczty')).to_be_visible()
                            expect(header.get_by_role('button',name='Opróżnij',exact=True)).to_be_visible()
                        if route=='settings':
                            expect(header.get_by_role('navigation',name='Kategorie ustawień')).to_be_visible()
                        if route=='dashboard':
                            self.page.locator('main button').filter(has_text='Nginx').click()
                            self.shot(prefix+'service-dialog')
                            self.page.locator('dialog:modal').get_by_role('button',name='Zamknij',exact=True).click()
                        if route=='projects':
                            self.page.locator('main button').filter(has_text='Browser fixture').click()
                            expect(self.page.get_by_role('heading',name='Browser fixture',exact=True)).to_be_visible()
                            self.shot(prefix+'project')
                            config=self.page.get_by_role('button',name=re.compile('pełną konfigurację'))
                            if config.get_attribute('aria-expanded')!='true':config.click()
                            self.shot(prefix+'project-nginx')
                            self.goto('projects')
                            self.page.locator('main button').filter(has_text='A'*70).click()
                            self.shot(prefix+'project-long-name')
                        if route=='new':
                            self.page.get_by_role('combobox',name='Framework',exact=True).select_option('laravel')
                            expect(self.page.get_by_role('combobox',name='Technologia frontendowa')).to_be_enabled(timeout=60000)
                            self.page.get_by_role('combobox',name='Technologia frontendowa').select_option('livewire')
                            self.shot(prefix+'new-laravel')
                        if route=='mail':
                            self.page.get_by_role('button',name='Konfiguracja poczty').click()
                            self.page.get_by_text('Istniejący projekt Laravel (.env)',exact=True).click()
                            self.shot(prefix+'mail-settings')
                            self.page.get_by_role('button',name='Zamknij konfigurację').click()
                            self.message_button(HTML_SUBJECT).click()
                            expect(self.page.frame_locator('iframe[title="Podgląd wiadomości HTML"]').get_by_role('heading',name='Potwierdzenie rezerwacji')).to_be_visible()
                            self.shot(prefix+'mail-html')
                            self.page.get_by_role('button',name='Tekst',exact=True).click();self.shot(prefix+'mail-text')
                            self.page.get_by_role('button',name='Źródło HTML',exact=True).click();self.shot(prefix+'mail-source')
                        if route=='settings':
                            for label in ['Środowisko','Instalatory','Windows','Widżety','Powiadomienia','Bezpieczeństwo']:
                                self.page.get_by_role('navigation',name='Kategorie ustawień').get_by_role('button',name=label,exact=True).click()
                                if label=='Instalatory':
                                    expect(self.page.locator('section[aria-label="Instalatory"]').get_by_text('Odczytywanie…',exact=True)).to_be_hidden(timeout=60000)
                                self.shot(prefix+'settings-'+label)
        self.goto('dashboard');self.page.reload()
        expect(self.page.locator('html')).to_have_class(re.compile(r'\bdark\b'))

    def test_02_mail_rendering_download_search_pagination_and_deletion(self):
        for i in range(42):send(f'Pagination fixture {i:02}')
        send(HTML_SUBJECT, HTML)
        self.wait_total(43)
        self.goto('mail')
        # Regression: verify mail rendering and the external image guard
        # while the main document really is controlled by the PWA worker.
        self.page.wait_for_function("navigator.serviceWorker.controller !== null")
        self.assertEqual(self.page.evaluate('caches.keys()'),[])
        expect(self.page.get_by_text('1–40 / 43',exact=True)).to_be_visible()
        for theme in ['light','dark']:
            self.theme(theme)
            self.shot('853-'+theme+'-mail-populated')
        single=self.page.get_by_role('checkbox',name='Zaznacz: '+HTML_SUBJECT,exact=True)
        single.check()
        self.assertTrue(self.page.get_by_role('checkbox',name='Zaznacz wszystkie wiadomości na stronie',exact=True).evaluate('e=>e.indeterminate'))
        single.uncheck()
        self.page.get_by_role('button',name='Dalej',exact=True).click()
        expect(self.page.get_by_text('41–43 / 43',exact=True)).to_be_visible()
        self.page.get_by_role('button',name='Wstecz',exact=True).click()
        self.message_button(HTML_SUBJECT).click()
        expect(self.page.frame_locator('iframe[title="Podgląd wiadomości HTML"]').get_by_role('heading',name='Potwierdzenie rezerwacji')).to_be_visible()
        article=self.page.locator('article')
        iframe = article.locator('iframe[title="Podgląd wiadomości HTML"]')
        expect(iframe).to_have_attribute('sandbox','')
        expect(iframe).to_have_attribute('referrerpolicy','no-referrer')
        preview = self.page.frame_locator('iframe[title="Podgląd wiadomości HTML"]')
        expect(preview.get_by_role('cell',name='123,45 zł')).to_be_visible()
        expect(preview.locator('strong').filter(has_text='Łukasz')).to_be_visible()
        expect(preview.locator('script,iframe,svg,math,form,base,link,meta[http-equiv]')).to_have_count(0)
        expect(preview.locator('a[href]')).to_have_count(0)
        expect(preview.locator('a.mail-button')).to_have_css('background-color','rgb(22, 22, 22)')
        expect(preview.locator('a.mail-button')).to_have_css('color','rgb(255, 255, 255)')
        expect(preview.locator('body')).to_have_css('background-color','rgb(248, 250, 252)')
        expect(preview.locator('.mail-card')).to_have_css('background-color','rgb(255, 255, 255)')
        logo=preview.locator('img[alt="Fixture logo"]')
        expect(logo).to_have_attribute('src',re.compile('^data:image/png;base64,'))
        expect(logo).to_be_visible()
        for _ in range(50):
            if logo.evaluate('e=>e.complete&&e.naturalWidth===1'):break
            self.page.wait_for_timeout(100)
        self.assertTrue(logo.evaluate('e=>e.complete&&e.naturalWidth===1'))
        expect(preview.locator('img[alt="Remote"]')).not_to_have_attribute('src',re.compile('.+'))
        self.assertTrue(iframe.evaluate('e=>e.contentDocument===null'))
        self.assertIsNone(self.page.evaluate('window.mailAttack'))
        self.assert_mail_network()
        self.assertEqual(self.fixture_image_requests, [], 'Default preview must not load external images')
        self.shot('853-original-styles-mail')
        # Only the exact fixture image URLs may reach the context guard after
        # opt-in. No network access or blanket allowance of failed requests.
        self.external_images_enabled = True
        self.page.get_by_role('button',name='Wczytaj obrazy zewnętrzne').click()
        remote=preview.locator('img[alt="Remote"]')
        expect(remote).to_have_attribute('src','https://example.invalid/tracker')
        expect(remote).to_be_visible()
        for _ in range(100):
            if ({req.url for req in self.fixture_image_requests} == FIXTURE_IMAGES
                    and remote.evaluate('e=>e.complete&&e.naturalWidth===1')):break
            self.page.wait_for_timeout(100)
        self.assertEqual({req.url for req in self.fixture_image_requests}, FIXTURE_IMAGES)
        self.assertTrue(remote.evaluate('e=>e.complete&&e.naturalWidth===1'))
        self.assert_mail_network()
        approved_count=len(self.fixture_image_requests)
        self.external_images_enabled = False
        self.page.get_by_role('button',name='Ukryj obrazy zewnętrzne').click()
        expect(remote).not_to_have_attribute('src',re.compile('.+'))
        expect(preview.locator('body')).to_have_css('background-color','rgb(248, 250, 252)')
        self.assertEqual(len(self.fixture_image_requests), approved_count, 'Disabling images must stop new external requests')
        self.assert_mail_network()
        with self.page.expect_download() as download:
            article.get_by_role('link').filter(has_text='fixture.txt').click()
        self.assertEqual(pathlib.Path(download.value.path()).read_bytes(),b'Exact fixture bytes\n')
        message_id=next(m['ID'] for m in mailpit()['messages'] if m['Subject']==HTML_SUBJECT)
        message=self.context.request.get(BASE+'/api/mail/message/'+message_id).json()['result']
        part=next(a['PartID'] for a in message['Attachments'] if a['FileName']=='fixture.txt')
        attachment=self.context.request.get(BASE+'/api/mail/message/'+message_id+'/part/'+part)
        self.assertEqual(attachment.headers['content-type'],'application/octet-stream')
        self.assertEqual(attachment.headers['content-disposition'],'attachment')
        response=self.context.request.get(BASE+'/api/mail/message/'+message_id+'/preview')
        self.assertEqual(response.status,200)
        self.assertIn("sandbox; script-src 'none'",response.headers['content-security-policy'])
        self.assertIn('img-src data:;',response.headers['content-security-policy'])
        enabled=self.context.request.get(BASE+'/api/mail/message/'+message_id+'/preview?images=1')
        self.assertEqual(enabled.status,200)
        self.assertIn('img-src data: https:;',enabled.headers['content-security-policy'])
        self.assertIn("script-src 'none'",enabled.headers['content-security-policy'])
        self.assertEqual(response.headers['x-frame-options'],'SAMEORIGIN')
        self.assertIn("frame-ancestors 'none'",self.context.request.get(BASE+'/api/state').headers['content-security-policy'])
        self.assertEqual(self.context.request.get(BASE+'/api/state').headers['x-frame-options'],'DENY')
        self.page.get_by_role('button',name='Tekst',exact=True).click()
        expect(article.get_by_text('Plain fixture body — Zażółć gęślą jaźń',exact=False)).to_be_visible()
        self.page.get_by_role('button',name='Źródło HTML',exact=True).click()
        expect(article.locator('pre')).to_contain_text('<script>window.mailAttack=1</script>')
        self.page.get_by_role('button',name='Usuń',exact=True).click()
        self.page.get_by_role('button',name='Anuluj',exact=True).click()
        self.assertEqual(mailpit()['total'],43)
        self.page.get_by_role('button',name='Usuń',exact=True).click()
        self.page.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(self.page.locator('[data-slot="toast"]').get_by_text('Usunięto wiadomość.',exact=True)).to_be_visible()
        expect(self.page.locator('main').get_by_text('Usunięto wiadomość.',exact=True)).to_have_count(0)
        self.wait_total(42)
        self.page.get_by_role('textbox',name='Szukaj wiadomości').fill('Pagination fixture 00')
        self.page.get_by_role('button',name='Szukaj',exact=True).click()
        expect(self.page.get_by_text('1 wyników',exact=True)).to_be_visible()
        self.page.get_by_role('button',name='Wyczyść wyszukiwanie',exact=True).click()
        expect(self.page.get_by_role('textbox',name='Szukaj wiadomości')).to_have_value('')
        expect(self.page.get_by_text('1–40 / 42',exact=True)).to_be_visible()
        self.page.get_by_role('textbox',name='Szukaj wiadomości').fill('Pagination fixture 00')
        self.page.get_by_role('button',name='Szukaj',exact=True).click()
        expect(self.page.get_by_text('1 wyników',exact=True)).to_be_visible()
        self.page.get_by_role('checkbox',name='Zaznacz wszystkie wiadomości na stronie').check()
        self.page.get_by_role('button',name='Usuń (1)',exact=True).click()
        self.page.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(self.page.get_by_text('Brak wyników.',exact=True)).to_be_visible()
        self.wait_total(41)
        # Clearing is global even when the current search has no results.
        self.page.get_by_role('button',name='Opróżnij',exact=True).click()
        expect(self.page.get_by_text('Trwale usunąć wszystkie wiadomości ze wszystkich projektów, także poza aktualnym wyszukiwaniem?',exact=True)).to_be_visible()
        self.page.get_by_role('button',name='Anuluj',exact=True).click()
        self.assertEqual(mailpit()['total'],41)
        self.page.get_by_role('button',name='Opróżnij',exact=True).click()
        self.page.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(self.page.get_by_text('Skrzynka jest pusta.',exact=True)).to_be_visible()
        self.wait_total(0)
        send('Polling fixture')
        expect(self.message_button('Polling fixture')).to_be_visible(timeout=22000)

    def test_03_session_csrf_origin_validation_and_rate_limit(self):
        send('Security fixture')
        self.wait_total(1)
        anonymous=self.browser.new_context()
        self.addCleanup(anonymous.close)
        for path in ['/api/mail/messages','/api/mail/message/'+'A'*22]:
            self.assertEqual(anonymous.request.get(BASE+path).status,401)
        token=anonymous.request.get(BASE+'/api/session').json()['csrf']
        self.assertEqual(anonymous.request.post(BASE+'/api/mail/clear',data={'confirmation':'delete-all'},headers={'Origin':BASE,'X-CSRF-Token':token}).status,401)
        for headers in [{'Origin':BASE},{'Origin':'https://evil.invalid','X-CSRF-Token':self.csrf}]:
            self.assertEqual(self.context.request.post(BASE+'/api/mail/clear',data={'confirmation':'delete-all'},headers=headers).status,403)
        for path,data in [('/api/mail/clear',{}),('/api/mail/clear',{'confirmation':True}),('/api/mail/delete-many',{'ids':[]}),('/api/mail/delete-many',{'ids':['latest']}),('/api/mail/delete-many',{'ids':['A'*22]*41}),('/api/mail/delete',{'id':'../messages'})]:
            self.assertEqual(self.post(path,data).status,422)
        self.assertEqual(self.context.request.get(BASE+'/api/mail/messages?query=%00').status,422)
        self.assertEqual(self.post('/api/mail/clear',[]).status,400)
        self.assertEqual(self.post('/api/mail/clear',{'confirmation':'x'*17000}).status,413)
        self.assertEqual(self.context.request.get(BASE+'/api/mail/message/latest').status,404)
        self.assertEqual(self.context.request.get(BASE+'/api/mail/relay').status,404)
        response=self.context.request.get(BASE+'/api/mail/messages')
        self.assertIn("default-src 'none'",response.headers['content-security-policy'])
        self.assertIn('no-store',response.headers['cache-control'])
        message_id=mailpit()['messages'][0]['ID']
        self.assertEqual(self.context.request.get(BASE+'/api/mail/message/'+message_id+'/part/..%2Fraw').status,404)
        self.assertEqual(mailpit()['total'],1)
        # A successful login must rotate both the session cookie and CSRF token.
        token=anonymous.request.get(BASE+'/api/session').json()['csrf']
        cookies=anonymous.cookies()
        self.assertTrue(all(c['secure'] and c['httpOnly'] and c['sameSite']=='Strict' for c in cookies))
        before=cookies[0]['value']
        login=anonymous.request.post(BASE+'/api/login',data={'password':os.environ['DEVSTACK_E2E_PASSWORD']},headers={'Origin':BASE,'X-CSRF-Token':token})
        self.assertEqual(login.status,200,login.text())
        self.assertNotEqual(login.json()['csrf'],token)
        self.assertNotEqual(anonymous.cookies()[0]['value'],before)
        anonymous.request.post(BASE+'/api/logout',data={},headers={'Origin':BASE,'X-CSRF-Token':login.json()['csrf']})
        token=anonymous.request.get(BASE+'/api/session').json()['csrf']
        for _ in range(8):
            self.assertEqual(anonymous.request.post(BASE+'/api/login',data={'password':'incorrect-fixture-password'},headers={'Origin':BASE,'X-CSRF-Token':token}).status,401)
        self.assertEqual(anonymous.request.post(BASE+'/api/login',data={'password':'incorrect-fixture-password'},headers={'Origin':BASE,'X-CSRF-Token':token}).status,429)

    def test_04_navigation_forms_widgets_and_service_confirmation(self):
        self.goto('dashboard')
        expect(self.page.get_by_role('button',name='Nowy projekt',exact=True)).to_have_count(0)
        icon = self.page.locator('link[rel="icon"]').get_attribute('href')
        self.assertTrue(icon.startswith('/assets/') and icon.endswith('.svg'), icon)
        icon_response = self.context.request.get(BASE + icon)
        self.assertEqual(icon_response.status,200)
        self.assertIn('image/svg+xml',icon_response.headers['content-type'])
        self.assertIn('M10 22V7',icon_response.text())
        opener=self.page.get_by_role('button',name='Otwórz menu')
        opener.click()
        expect(self.page.get_by_role('dialog',name='Menu główne')).to_be_visible()
        self.page.keyboard.press('Shift+Tab')
        self.assertTrue(self.page.locator('aside').evaluate('e=>e.contains(document.activeElement)'))
        self.page.keyboard.press('Escape')
        expect(opener).to_be_focused()
        self.assertTrue(self.page.locator('aside').evaluate('e=>e.inert'))
        opener.click()
        self.page.get_by_role('navigation',name='Nawigacja główna').get_by_role('button',name='Projekty',exact=False).click()
        expect(self.page.get_by_role('heading',name='Projekty',exact=True)).to_be_visible()
        self.page.get_by_role('link',name='DevStack',exact=True).click()
        expect(self.page.get_by_role('heading',name='Przegląd',exact=True)).to_be_visible()
        expect(self.page.get_by_role('button',name='Nowy projekt',exact=True)).to_have_count(0)
        self.goto('projects')
        self.page.get_by_role('button',name='Nowy projekt',exact=True).click()
        self.page.get_by_role('textbox',name='Nazwa projektu',exact=True).fill('New browser fixture')
        expect(self.page.get_by_role('textbox',name='Identyfikator katalogu')).to_have_value('new-browser-fixture')
        expect(self.page.get_by_role('textbox',name='Domena .test')).to_have_value('new-browser-fixture.test')
        expect(self.page.get_by_role('checkbox',name='Lokalna poczta testowa',exact=True)).not_to_be_checked()
        self.page.get_by_role('checkbox',name='Lokalna poczta testowa',exact=True).check()
        # Exercise the compact page-header submit button outside the form.
        self.page.get_by_role('button',name='Utwórz projekt',exact=True).first.click()
        expect(self.page.get_by_role('heading',name='New browser fixture',exact=True)).to_be_visible(timeout=30000)
        self.goto('new')
        self.page.get_by_role('combobox',name='Framework',exact=True).select_option('laravel')
        self.page.get_by_role('button',name='Zarządzaj oficjalnym instalatorem w ustawieniach').click()
        expect(self.page.get_by_role('heading',name='Narzędzia instalacyjne')).to_be_visible()
        self.page.get_by_role('navigation',name='Kategorie ustawień').get_by_role('button',name='Widżety',exact=True).click()
        box=self.page.get_by_role('checkbox').last
        checked=box.is_checked();box.set_checked(not checked)
        self.page.get_by_role('button',name='Zapisz wybór').click()
        expect(self.page.locator('[data-slot="toast"]').get_by_text('Zapisano wybór usług.',exact=True)).to_be_visible()
        self.page.reload()
        self.page.get_by_role('navigation',name='Kategorie ustawień').get_by_role('button',name='Widżety',exact=True).click()
        self.assertEqual(self.page.get_by_role('checkbox').last.is_checked(),not checked)
        self.goto('dashboard')
        self.page.locator('main button').filter(has_text='Redis').click()
        dialog=self.page.locator('dialog:modal')
        dialog.get_by_role('button',name='Zatrzymaj',exact=True).click()
        dialog.get_by_role('button',name='Anuluj',exact=True).click()
        dialog.get_by_role('button',name='Zatrzymaj',exact=True).click()
        dialog.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(dialog.locator('[data-slot="toast"]').filter(has_text='Polecenie przyjęte:')).to_be_visible()
        expect(dialog.get_by_text('Zatrzymana',exact=True)).to_be_visible(timeout=12000)
        dialog.get_by_role('button',name='Uruchom',exact=True).click()
        dialog.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(dialog.get_by_text('Działa',exact=True)).to_be_visible(timeout=12000)

    def test_07_global_toasts_and_job_presentation(self):
        # Real operations for the shared renderer; separately replay only a
        # synthetic job status through GET /api/state for deterministic UI
        # lifecycle coverage. All writes and authentication are real.
        self.goto('dashboard')
        self.page.set_viewport_size({'width':640,'height':800})
        toasts=self.page.locator('[data-slot="toast"]')
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(toasts.get_by_text('Odświeżono dane.',exact=True)).to_be_visible()
        self.page.get_by_role('button',name=re.compile('Włącz (jasny|ciemny) motyw')).click()
        expect(toasts.filter(has_text='Zapisano').first).to_be_visible()
        expect(toasts.get_by_text('Odświeżono dane.',exact=True)).to_be_visible()
        self.page.get_by_role('button',name='Otwórz menu').click()
        self.page.get_by_role('navigation',name='Nawigacja główna').get_by_role('button',name='Projekty',exact=False).click()
        expect(self.page.get_by_role('heading',name='Projekty',exact=True)).to_be_visible()
        expect(toasts.get_by_text('Odświeżono dane.',exact=True)).to_be_visible()
        self.assertTrue(toasts.first.evaluate("e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}"))
        expect(self.page.locator('main').get_by_text('Odświeżono dane.',exact=True)).to_have_count(0)

        # Actual API state remains the basis; override the job alone to test
        # long-running and failed/completed rendering without fetching packages.
        job={'id':'a'*32,'engine':'laravel','action':'create','state':'scaffolding','message':'Instalacja testowa w toku…','slug':'browser-fixture'}
        def job_response(route):
            response=route.fetch()
            body=response.json()
            body['result']['engine_job']=dict(job)
            route.fulfill(response=response,json=body)
        self.page.route('**/api/state',job_response)
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        installer=toasts.filter(has_text='Instalacja projektu')
        expect(installer).to_have_count(1)
        expect(installer.get_by_text('Instalacja testowa w toku…',exact=False)).to_be_visible()
        expect(installer).to_have_attribute('data-toast-type','loading')
        expect(installer.get_by_role('button',name='Zamknij powiadomienie')).to_have_count(0)
        installer.evaluate("e=>e.dataset.lifecycleTest='same-node'")
        # The running toast outlives the ordinary six-second timeout, even
        # when the user is not hovering/focusing it. Escape cannot dismiss it.
        installer.focus()
        self.page.keyboard.press('Escape')
        self.page.locator('main h1').first.evaluate("e=>{e.tabIndex=-1;e.focus()}")
        self.page.mouse.move(1,1)
        self.page.wait_for_timeout(6800)
        expect(installer).to_be_visible()
        job.update(state='configuring',message='Konfiguracja projektu w toku…')
        expect(installer.get_by_text('Konfiguracja projektu w toku…',exact=False)).to_be_visible(timeout=12000)
        expect(installer).to_have_attribute('data-lifecycle-test','same-node')
        job.update(state='succeeded',message='Środowisko testowe gotowe.')
        expect(installer.get_by_text('Środowisko testowe gotowe.',exact=False)).to_be_visible(timeout=12000)
        expect(installer).to_have_attribute('data-toast-type','success')
        expect(installer).to_have_attribute('data-lifecycle-test','same-node')
        expect(installer).to_have_count(1)
        expect(installer.get_by_role('button',name='Przejdź do projektu')).to_be_visible()
        self.page.wait_for_timeout(2500)
        expect(installer).to_be_visible()
        # Repeated reads of the same completed job must not restart its timer.
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        self.page.mouse.move(1,1)
        expect(installer).to_have_count(0,timeout=5500)
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(installer).to_have_count(0)
        # A distinct success still exposes the existing project action.
        job.update(id='d'*32,state='succeeded',message='Drugi wynik testowy gotowy.')
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(installer.get_by_text('Drugi wynik testowy gotowy.',exact=False)).to_be_visible()
        installer.get_by_role('button',name='Przejdź do projektu').click()
        expect(self.page.get_by_role('heading',name='Browser fixture',exact=True)).to_be_visible()
        self.page.reload()
        expect(self.page.locator('main')).to_be_visible()
        expect(installer).to_have_count(0)
        job.update(id='b'*32,state='failed',message='Instalacja testowa nieudana.',log='fixture.log')
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(installer.get_by_text('Instalacja testowa nieudana.',exact=False)).to_be_visible()
        expect(installer.get_by_text('fixture.log',exact=False)).to_be_visible()
        self.page.reload()
        expect(installer.get_by_text('Instalacja testowa nieudana.',exact=False)).to_be_visible()
        # A newer job replaces the previous installer toast, not other
        # independent notices. The previous failure is not acknowledged.
        job.update(id='c'*32,state='failed',message='Nowszy błąd instalacji.',log='new-fixture.log')
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(installer.get_by_text('Nowszy błąd instalacji.',exact=False)).to_be_visible()
        expect(installer).to_have_count(1)
        # Full cards remain expanded visually even when the pointer and focus
        # are elsewhere. Close must be discoverable by its accessible role
        # without hovering, forcing the click, or including hidden elements.
        self.page.mouse.move(1,1)
        close = installer.get_by_role('button',name='Zamknij powiadomienie',exact=True)
        expect(close).to_be_visible()
        expect(close).to_have_attribute('aria-hidden','false')
        close.focus()
        expect(close).to_be_focused()
        self.page.keyboard.press('Enter')
        expect(installer).to_have_count(0)
        self.assertEqual(self.page.evaluate("localStorage.getItem('devstack.engine-job-notice.v1')"), 'failed:'+'c'*32)
        self.page.reload()
        expect(self.page.locator('main')).to_be_visible()
        expect(installer).to_have_count(0)
        self.page.unroute('**/api/state',job_response)

    def test_08_pwa_and_notification_history(self):
        manifest=self.context.request.get(BASE+'/manifest.webmanifest')
        self.assertEqual(manifest.status,200)
        info=manifest.json()
        self.assertEqual(info['display'],'standalone')
        self.assertEqual(info['scope'],'/')
        self.assertEqual(info['id'],'/')
        self.assertEqual(info['start_url'],'/#/dashboard')
        for icon in info['icons']:
            response=self.context.request.get(BASE+icon['src'])
            self.assertEqual(response.status,200)
            if icon['type']=='image/png':self.assertTrue(response.body().startswith(b'\x89PNG\r\n\x1a\n'))
        self.goto('dashboard')
        self.page.wait_for_function("navigator.serviceWorker.controller !== null")
        self.assertTrue(self.page.evaluate("navigator.serviceWorker.getRegistration('/').then(r=>!!r?.active)"))
        self.assertEqual(self.page.evaluate('caches.keys()'),[])
        # Establish the empty-mailbox baseline and finish any prior worker
        # pass before clearing the independent event history.
        subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
        self.assertEqual(self.post('/api/notifications/manage',{'action':'clear'}).status,200)
        self.page.get_by_role('button',name='Powiadomienia',exact=True).click()
        panel=self.page.get_by_role('region',name='Historia powiadomień')
        expect(panel.get_by_text('Brak powiadomień',exact=True)).to_be_visible()
        # The mailbox baseline is established even when Mailpit returns null
        # instead of an empty messages array.
        send('Notification fixture alpha')
        self.wait_total(1)
        subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
        item=panel.locator('[data-notification-id]').filter(has_text='Notification fixture alpha')
        expect(item).to_have_count(1,timeout=20000)
        expect(self.page.locator('[data-slot="toast"]').filter(has_text='Notification fixture alpha')).to_be_visible(timeout=10000)
        second=self.context.new_page()
        try:
            second.goto(BASE+'/#/dashboard')
            expect(second.locator('main')).to_be_visible()
            second.get_by_role('button',name='Powiadomienia',exact=True).click()
            other=second.get_by_role('region',name='Historia powiadomień')
            expect(other.locator('[data-notification-id]').filter(has_text='Notification fixture alpha')).to_have_count(1)
            expect(second.locator('[data-slot="toast"]').filter(has_text='Notification fixture alpha')).to_have_count(0)
            item.get_by_role('button',name='Usuń powiadomienie: Nowa wiadomość e-mail').click()
            expect(item).to_have_count(0)
            expect(other.locator('[data-notification-id]').filter(has_text='Notification fixture alpha')).to_have_count(0,timeout=10000)
            self.assertEqual(mailpit()['total'],1)
            subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
            expect(panel.get_by_text('Brak powiadomień',exact=True)).to_be_visible()
            second.close()
            send('Notification fixture beta')
            self.wait_total(2)
            subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
            item=panel.locator('[data-notification-id]').filter(has_text='Notification fixture beta')
            expect(item).to_have_count(1,timeout=20000)
            item.get_by_role('button').filter(has_text='Notification fixture beta').click()
            expect(self.page.get_by_role('heading',name='Notification fixture beta')).to_be_visible()
            self.page.get_by_role('button',name='Powiadomienia',exact=True).click()
            expect(panel.get_by_text('0 nowych',exact=True)).to_be_visible()
            # Simulate the exact same-origin fragment produced by the actual
            # notificationclick handler (unit tested separately). No fake
            # provider delivery or OS banner is claimed by this browser test.
            send('Notification fixture gamma')
            self.wait_total(3)
            subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
            gamma=panel.locator('[data-notification-id]').filter(has_text='Notification fixture gamma')
            expect(gamma).to_have_count(1,timeout=20000)
            gamma_id=gamma.get_attribute('data-notification-id')
            send('Notification fixture delta')
            self.wait_total(4)
            subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
            delta=panel.locator('[data-notification-id]').filter(has_text='Notification fixture delta')
            expect(delta).to_have_count(1,timeout=20000)
            delta_id=delta.get_attribute('data-notification-id')
            expect(panel.get_by_text('2 nowych',exact=True)).to_be_visible()
            panel.get_by_role('button',name='Zamknij historię').click()
            self.page.goto(BASE+'/#/mail/'+gamma_id[5:]+'?notice='+gamma_id)
            expect(self.page.get_by_role('heading',name='Notification fixture gamma')).to_be_visible()
            expect(self.page).to_have_url(BASE+'/#/mail/'+gamma_id[5:])
            self.page.get_by_role('button',name='Powiadomienia',exact=True).click()
            expect(panel.get_by_text('1 nowych',exact=True)).to_be_visible()
            expect(gamma.locator('[aria-label="Nieprzeczytane"]')).to_have_count(0)
            expect(delta.locator('[aria-label="Nieprzeczytane"]')).to_have_count(1)
            # A cold launch without a session must preserve the click across
            # password login; it must not mark the event before authentication.
            cold=self.browser.new_context()
            try:
                launch=cold.new_page()
                launch.goto(BASE+'/#/mail/'+delta_id[5:]+'?notice='+delta_id)
                expect(launch.get_by_role('heading',name='Logowanie',exact=True)).to_be_visible()
                expect(delta.locator('[aria-label="Nieprzeczytane"]')).to_have_count(1)
                launch.get_by_label('Hasło administratora',exact=True).fill(os.environ['DEVSTACK_E2E_PASSWORD'])
                launch.get_by_role('button',name='Zaloguj się',exact=True).click()
                expect(launch.get_by_role('heading',name='Notification fixture delta')).to_be_visible()
                expect(launch).to_have_url(BASE+'/#/mail/'+delta_id[5:])
                launch.get_by_role('button',name='Powiadomienia',exact=True).click()
                expect(launch.get_by_role('region',name='Historia powiadomień').get_by_text('0 nowych',exact=True)).to_be_visible()
                expect(panel.get_by_text('0 nowych',exact=True)).to_be_visible(timeout=10000)
            finally:
                cold.close()
            # Opening a real toast, not the history entry, marks only that
            # event read and navigates to its mail. Keep an unrelated unread.
            send('Notification fixture epsilon')
            self.wait_total(5)
            subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
            epsilon=panel.locator('[data-notification-id]').filter(has_text='Notification fixture epsilon')
            expect(epsilon).to_have_count(1,timeout=20000)
            send('Notification fixture zeta')
            self.wait_total(6)
            subprocess.run(['sudo','systemctl','start','devstack-notifications.service'],check=True)
            zeta=panel.locator('[data-notification-id]').filter(has_text='Notification fixture zeta')
            expect(zeta).to_have_count(1,timeout=20000)
            expect(panel.get_by_text('2 nowych',exact=True)).to_be_visible()
            panel.get_by_role('button',name='Zamknij historię').click()
            zeta_toast=self.page.locator('[data-slot="toast"]').filter(has_text='Notification fixture zeta')
            zeta_toast.get_by_role('button',name='Otwórz',exact=True).click()
            expect(self.page.get_by_role('heading',name='Notification fixture zeta')).to_be_visible()
            self.page.get_by_role('button',name='Powiadomienia',exact=True).click()
            expect(panel.get_by_text('1 nowych',exact=True)).to_be_visible()
            expect(epsilon.locator('[aria-label="Nieprzeczytane"]')).to_have_count(1)
            expect(zeta.locator('[aria-label="Nieprzeczytane"]')).to_have_count(0)
            panel.get_by_role('button',name='Wyczyść wszystko',exact=True).click()
            expect(panel.get_by_text('Brak powiadomień',exact=True)).to_be_visible()
            self.page.reload()
            expect(self.page.locator('main')).to_be_visible()
            self.page.get_by_role('button',name='Powiadomienia',exact=True).click()
            expect(panel.get_by_text('Brak powiadomień',exact=True)).to_be_visible()
            self.assertEqual(mailpit()['total'],6)
        finally:
            if not second.is_closed():second.close()
        anonymous=self.browser.new_context()
        try:
            self.assertEqual(anonymous.request.post(BASE+'/api/notifications/poll',data={'client':'a'*32}).status,403)
        finally:anonymous.close()

    def test_09_notification_presence_lifecycle(self):
        # Inspect only the isolated fixture's client IDs, never push secrets.
        def present(client):
            script=("import sqlite3,sys,time;"
                    "db=sqlite3.connect('file:/var/lib/devstack-web/notifications.sqlite3?mode=ro',uri=True);"
                    "print(db.execute('SELECT COUNT(*) FROM clients WHERE id=? AND expires>?',"
                    "(sys.argv[1],int(time.time()))).fetchone()[0])")
            return subprocess.check_output(['sudo','-u','devstackweb','python3','-c',script,client],text=True).strip()=='1'

        def wait_presence(client,expected):
            for _ in range(100):
                if present(client)==expected:return
                self.page.wait_for_timeout(100)
            self.fail('Unexpected lease state for '+client)

        def poll_response(response):
            return response.url==BASE+'/api/notifications/poll' and response.status==200

        with self.page.expect_response(poll_response) as first:
            self.goto('dashboard')
        original=first.value.request.post_data_json['client']
        self.assertTrue(present(original))
        # Beacon has no custom header. The body-token exception is restricted
        # to this exact same-origin JSON endpoint and still uses the session
        # token. Bad requests cannot remove the live document.
        leave_url=BASE+'/api/notifications/leave'
        for headers,body in [
            ({'Origin':BASE},{'client':original,'csrf':'invalid'}),
            ({'Origin':'https://evil.invalid'},{'client':original,'csrf':self.csrf}),
            ({},{'client':original,'csrf':self.csrf}),
            ({'Origin':BASE,'Content-Type':'text/plain'},{'client':original,'csrf':self.csrf}),
            ({'Origin':BASE},{'client':original,'csrf':self.csrf,'extra':True}),
            ({'Origin':BASE,'X-CSRF-Token':'invalid'},{'client':original,'csrf':self.csrf}),
        ]:
            self.assertEqual(self.context.request.post(leave_url,headers=headers,data=body).status,403)
        self.assertEqual(self.context.request.post(BASE+'/api/notifications/manage',
            headers={'Origin':BASE},data={'action':'read-all','csrf':self.csrf}).status,403)
        self.assertTrue(present(original))
        probe='c'*32
        self.assertEqual(self.post('/api/notifications/poll',{'client':probe}).status,200)
        self.assertTrue(present(probe))
        self.assertEqual(self.context.request.post(leave_url,headers={'Origin':BASE},
            data={'client':probe,'csrf':self.csrf}).status,200)
        self.assertFalse(present(probe))
        second=self.context.new_page()
        try:
            with second.expect_response(poll_response) as other:
                second.goto(BASE+'/#/dashboard')
            client=other.value.request.post_data_json['client']
            self.assertNotEqual(original,client)
            self.assertTrue(present(client))
            requests=[]
            second.on('request',lambda req: requests.append(req.url) if req.url==BASE+'/api/notifications/poll' else None)
            # Drive the actual pagehide handler deterministically, leaving the
            # test document available to exercise a BFCache-style restoration.
            with second.expect_response(lambda r:r.url==BASE+'/api/notifications/leave' and r.status==200) as leave:
                second.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
            self.assertEqual(leave.value.request.post_data_json['client'],client)
            wait_presence(client,False)
            self.assertTrue(present(original))
            # Replay an older request at the authenticated API boundary.
            stale=self.post('/api/notifications/poll',{'client':client})
            self.assertEqual(stale.status,200)
            self.assertEqual(stale.json()['result']['toast'],[])
            self.assertEqual(self.context.request.get(BASE+'/api/state',headers={'X-DevStack-Client':client}).status,200)
            self.assertFalse(present(client))
            before=len(requests)
            second.evaluate("window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('hashchange')); document.dispatchEvent(new Event('visibilitychange'))")
            second.wait_for_timeout(5500) # Cross the real 5s heartbeat interval.
            self.assertEqual(len(requests),before,'Closed document started another poll')
            with second.expect_response(poll_response) as restored:
                second.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
            resumed=restored.value.request.post_data_json['client']
            self.assertNotEqual(resumed,client)
            self.assertTrue(present(resumed))
            self.assertFalse(present(client))
            # A genuine reload also releases the previous lifetime. Verify
            # that pagehide records the retired ID for the new document;
            # this covers the teardown-request loss seen in the previous CI.
            with second.expect_response(poll_response) as reloaded:
                second.reload()
            fresh=reloaded.value.request.post_data_json['client']
            self.assertNotIn(fresh,[original,client,resumed])
            self.assertIn(resumed,reloaded.value.request.post_data_json.get('retired',[]))
            wait_presence(resumed,False)
            self.assertTrue(present(fresh))
            self.assertTrue(present(original))
            # Deliberately lose the next beacon: a true result merely means
            # "queued". The real next authenticated poll must recover it.
            second.evaluate("Object.defineProperty(navigator,'sendBeacon',{configurable:true,value:()=>true})")
            with second.expect_response(poll_response) as recovered:
                second.reload()
            latest=recovered.value.request.post_data_json['client']
            self.assertNotIn(latest,[original,client,resumed,fresh])
            self.assertIn(fresh,recovered.value.request.post_data_json.get('retired',[]))
            wait_presence(fresh,False)
            self.assertTrue(present(latest))
            self.assertTrue(present(original))
            # Real navigation away with no DevStack successor: no recovery
            # poll is available, so the browser must deliver the leave itself.
            second.goto('about:blank')
            wait_presence(latest,False)
            self.assertTrue(present(original))
        finally:
            second.close()

    def test_05_login_screen_and_form(self):
        anonymous=self.browser.new_context(viewport={'width':853,'height':950})
        self.addCleanup(anonymous.close)
        login=anonymous.new_page()
        for theme in ['light','dark']:
            self.assertEqual(self.post('/api/preferences',{'theme':theme}).status,200)
            login.goto(BASE)
            expect(login.get_by_role('heading',name='Logowanie',exact=True)).to_be_visible()
            login.screenshot(path=str(OUT / ('853-'+theme+'-login.png')),full_page=True)
        login.get_by_label('Hasło administratora',exact=True).fill(os.environ['DEVSTACK_E2E_PASSWORD'])
        login.get_by_role('button',name='Zaloguj się',exact=True).click()
        expect(login.get_by_role('heading',name='Przegląd',exact=True)).to_be_visible()
        login.get_by_role('button',name='Otwórz menu').click()
        login.get_by_role('button',name='Wyloguj',exact=True).click()
        expect(login.get_by_role('heading',name='Logowanie',exact=True)).to_be_visible()
        expect(login.get_by_label('Hasło administratora',exact=True)).to_have_value('')
        expect(login.locator('body')).not_to_have_css('overflow','hidden')
        login.get_by_label('Hasło administratora',exact=True).fill(os.environ['DEVSTACK_E2E_PASSWORD'])
        login.get_by_role('button',name='Zaloguj się',exact=True).click()
        expect(login.get_by_role('heading',name='Przegląd',exact=True)).to_be_visible()
        self.assertTrue(login.locator('aside').evaluate('e=>e.inert'))

    def test_06_mailpit_offline_and_recovery(self):
        send('Retained through Mailpit stop')
        self.wait_total(1)

        def wait_state(state):
            for _ in range(100):
                value=self.context.request.get(BASE+'/api/state').json()['result']
                if value['mailpit']==state and not value.get('service_action_pending'):return
                time.sleep(.2)
            self.fail('Mailpit did not reach '+state)

        wait_state('active')
        self.assertEqual(self.post('/api/services/control',{'service':'mailpit','action':'stop','confirmed':True}).status,200)
        try:
            wait_state('inactive')
            self.goto('mail')
            expect(self.page.get_by_text('Mailpit jest zatrzymany',exact=True)).to_be_visible()
            for theme in ['light','dark']:
                self.theme(theme)
                self.shot('853-'+theme+'-mail-offline')
                self.page.get_by_role('button',name='Konfiguracja poczty',exact=True).click()
                expect(self.page.locator('dialog:modal').get_by_text('sudo systemctl start devstack-mailpit.service',exact=True)).to_be_visible()
                self.page.get_by_role('button',name='Zamknij konfigurację',exact=True).click()
        finally:
            self.assertEqual(self.post('/api/services/control',{'service':'mailpit','action':'start','confirmed':True}).status,200)
            wait_state('active')
        self.page.get_by_role('button',name='Odśwież dane usług i projektów',exact=True).click()
        expect(self.message_button('Retained through Mailpit stop')).to_be_visible()


if __name__=='__main__':
    # Rate limiting is tested last because it is deliberately shared by all sessions.
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BrowserAcceptance)
    tests=list(suite)
    tests.sort(key=lambda t:t._testMethodName=='test_03_session_csrf_origin_validation_and_rate_limit')
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
    (OUT / 'report.json').write_text(json.dumps({'tests':result.testsRun,'failures':[(str(t),msg) for t,msg in result.failures],'errors':[(str(t),msg) for t,msg in result.errors],'success':result.wasSuccessful()},ensure_ascii=False,indent=2))
    raise SystemExit(not result.wasSuccessful())
