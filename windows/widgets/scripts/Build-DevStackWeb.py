#!/usr/bin/env python3
"""Extract the exact user-approved DevStack 03 layout; replace ONLY demo data/JS."""
from pathlib import Path
import re, gzip, hashlib, base64
ROOT=Path(__file__).resolve().parents[1]
APPROVED_SHA256='b1b31780ccc53cab97e3b29364f1dfb6ac0fe3fa832ebb72abb84fd9497c014e'
def build():
    archive=ROOT/'design/approved-devstack-03.html.gz'
    parts=sorted(archive.parent.glob(archive.name+'.[0-9][0-9]'))
    if len(parts)!=3:raise RuntimeError('Approved source parts missing')
    packed=b''.join(part.read_bytes() for part in parts)
    raw=gzip.decompress(packed)
    archive.write_bytes(packed)  # Assembled only for the visual-reference tests.
    if hashlib.sha256(raw).hexdigest()!=APPROVED_SHA256:raise RuntimeError('Approved DevStack prototype changed')
    source=raw.decode('utf-8')
    css=re.search(r'<style>(.*?)</style>',source,re.S).group(1)
    icons=re.search(r'<svg[^>]*aria-hidden="true"[^>]*>.*?</svg>',source,re.S).group(0).replace('style="display:none"','class="svg-defs"')
    card=re.search(r'<article\b[^>]*id="widget".*?</article>',source,re.S).group(0)
    card=card.replace('Interaktywny widżet DevStack, dane demonstracyjne','DevStack').replace('Odśwież dane demonstracyjne','Odśwież dane')
    card=card.replace('DANE DEMO','BRAK DANYCH').replace('Łącznie projektów: 3','Łącznie projektów: —').replace('00:29:59','—')
    card=card.replace('id="statusTitle">Gotowe do pracy','id="statusTitle">Sprawdzanie…').replace('Dostępne usługi: 4 z 4.','Odczytywanie stanu usług.')
    card=re.sub(r'(id="state-[^"]+">)Dostępne',r'\1Brak danych',card)
    card=re.sub(r'<div class="widget-menu".*?</div>','',card,flags=re.S)
    card=re.sub(r'<div class="sheet-backdrop".*?</section></div>','',card,flags=re.S)
    card=card.replace('id="restartServiceButton"','id="restartServiceButton" disabled')
    css+='\n/* Host boundary only. Approved DevStack card geometry is unchanged. */\n.svg-defs{position:absolute;width:0;height:0;overflow:hidden}\nhtml,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent!important}\nhtml[data-host="windows"]{border-radius:8px;overflow:hidden}\nhtml[data-host="windows"] body{border-radius:inherit;overflow:hidden;padding:0;display:block}\nhtml[data-host="windows"] .widget{position:relative;inset:auto;width:100%;height:100%;transform:none!important;box-shadow:none;border-radius:inherit;border-color:transparent}\nhtml[data-host="windows"][data-theme="dark"] .widget{background:transparent}\nhtml[data-host="windows"] #menuButton{visibility:hidden}\n'
    out=ROOT/'ServicesWeb';out.mkdir(exist_ok=True)
    css=css.replace('\r\n','\n').replace('\r','\n')
    js=(out/'services.js').read_text(encoding='utf-8').replace('\r\n','\n').replace('\r','\n')
    if '</script' in js.lower() or '</style' in css.lower():raise RuntimeError('Unsafe inline terminator')
    digest=lambda text:base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
    csp="default-src 'none'; style-src 'sha256-"+digest(css)+"'; script-src 'sha256-"+digest(js)+"'; img-src data: 'self'; connect-src 'self' https://devstack-services.invalid; font-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    page='<!doctype html><html lang="pl" data-theme="dark" data-host="windows" data-origin="https://devstack-services.invalid"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark light"><meta http-equiv="Content-Security-Policy" content="'+csp+'"><title>DevStack</title><style>'+css+'</style></head><body>'+icons+card+'<!--DEVSTACK_BOOTSTRAP--><script>'+js+'</script></body></html>'
    for name,text in [('services.css',css),('csp.txt',csp),('index.html',page),('unavailable.html','<!doctype html><html lang="pl"><meta charset="utf-8"><p>DevStack: otwórz panel ponownie.</p></html>')]:
        (out/name).write_bytes(text.encode('utf-8'))
    print('Approved DevStack 03 extracted; self-contained production document with pinned CSP generated.')
if __name__=='__main__':build()
