"""Destructive acceptance tests for a disposable, fully installed DevStack.

The app, PHP session boundary, Unix helper, Nginx, databases, and Mailpit are
real. No HTTP requests or browser APIs are mocked. Never use a personal inbox.
"""
import json
import os
import pathlib
import re
import smtplib
import time
import unittest
import urllib.request
from email.message import EmailMessage

from playwright.sync_api import expect, sync_playwright

BASE = 'https://devstack.test'
OUT = pathlib.Path('browser-results')
HTML_SUBJECT = 'HTML fixture — Zażółć gęślą jaźń'
HTML = '''<!doctype html><html><head><style>body{background:red}</style></head>
<body><h1>Potwierdzenie rezerwacji</h1><p>Cześć <strong>Łukasz</strong>! Zażółć gęślą jaźń.</p>
<table><tr><th>Usługa</th><th>Cena</th></tr><tr><td>Serwis</td><td>123,45 zł</td></tr></table>
<ul><li>Pierwszy punkt</li><li>Drugi punkt</li></ul><blockquote>Treść cytowana</blockquote>
<a href="https://example.invalid/reset">Potwierdź</a>
<img src="https://example.invalid/tracker" onerror="window.mailAttack=1">
<script>window.mailAttack=1</script><iframe srcdoc="bad"></iframe>
<svg onload="window.mailAttack=1"></svg><form action="https://example.invalid"><input autofocus></form>
</body></html>'''


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
        self.context = self.browser.new_context(viewport={'width':853, 'height':950}, locale='pl-PL')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.external = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda req: self.external.append(req.url) if not req.url.startswith(BASE + '/') else None)
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
        self.assertEqual(self.external, [], 'Unexpected external requests from message content')

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
        for b in self.page.locator('[role="status"] button[aria-label="Zamknij"]').all():b.click()

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
                            self.page.get_by_role('dialog').get_by_role('button',name='Zamknij',exact=True).click()
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
                            expect(self.page.get_by_role('heading',name='Potwierdzenie rezerwacji')).to_be_visible()
                            self.shot(prefix+'mail-html')
                            self.page.get_by_role('button',name='Tekst',exact=True).click();self.shot(prefix+'mail-text')
                            self.page.get_by_role('button',name='Źródło HTML',exact=True).click();self.shot(prefix+'mail-source')
                        if route=='settings':
                            for label in ['Środowisko','Instalatory','Windows','Widżety','Bezpieczeństwo']:
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
        expect(self.page.get_by_role('heading',name='Potwierdzenie rezerwacji')).to_be_visible()
        article=self.page.locator('article')
        expect(article.get_by_role('cell',name='123,45 zł')).to_be_visible()
        expect(article.locator('strong').filter(has_text='Łukasz')).to_be_visible()
        expect(article.locator('script,iframe,img,style,form')).to_have_count(0)
        expect(article.locator('a[href^="https://"]')).to_have_count(0)
        self.assertIsNone(self.page.evaluate('window.mailAttack'))
        with self.page.expect_download() as download:
            article.get_by_role('link').filter(has_text='fixture.txt').click()
        self.assertEqual(pathlib.Path(download.value.path()).read_bytes(),b'Exact fixture bytes\n')
        message_id=next(m['ID'] for m in mailpit()['messages'] if m['Subject']==HTML_SUBJECT)
        message=self.context.request.get(BASE+'/api/mail/message/'+message_id).json()['result']
        part=message['Attachments'][0]['PartID']
        attachment=self.context.request.get(BASE+'/api/mail/message/'+message_id+'/part/'+part)
        self.assertEqual(attachment.headers['content-type'],'application/octet-stream')
        self.assertEqual(attachment.headers['content-disposition'],'attachment')
        self.page.get_by_role('button',name='Tekst',exact=True).click()
        expect(article.get_by_text('Plain fixture body — Zażółć gęślą jaźń',exact=False)).to_be_visible()
        self.page.get_by_role('button',name='Źródło HTML',exact=True).click()
        expect(article.locator('pre')).to_contain_text('<script>window.mailAttack=1</script>')
        self.page.get_by_role('button',name='Usuń',exact=True).click()
        self.page.get_by_role('button',name='Anuluj',exact=True).click()
        self.assertEqual(mailpit()['total'],43)
        self.page.get_by_role('button',name='Usuń',exact=True).click()
        self.page.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(self.page.get_by_text('Usunięto wiadomość.',exact=True)).to_be_visible()
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
        expect(self.page.get_by_text('Zapisano wybór usług.',exact=True)).to_be_visible()
        self.page.reload()
        self.page.get_by_role('navigation',name='Kategorie ustawień').get_by_role('button',name='Widżety',exact=True).click()
        self.assertEqual(self.page.get_by_role('checkbox').last.is_checked(),not checked)
        self.goto('dashboard')
        self.page.locator('main button').filter(has_text='Redis').click()
        dialog=self.page.get_by_role('dialog')
        dialog.get_by_role('button',name='Zatrzymaj',exact=True).click()
        dialog.get_by_role('button',name='Anuluj',exact=True).click()
        dialog.get_by_role('button',name='Zatrzymaj',exact=True).click()
        dialog.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(dialog.get_by_text('Zatrzymana',exact=True)).to_be_visible(timeout=12000)
        dialog.get_by_role('button',name='Uruchom',exact=True).click()
        dialog.get_by_role('button',name='Potwierdź',exact=True).click()
        expect(dialog.get_by_text('Działa',exact=True)).to_be_visible(timeout=12000)

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
                expect(self.page.get_by_role('dialog').get_by_text('sudo systemctl start devstack-mailpit.service',exact=True)).to_be_visible()
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
