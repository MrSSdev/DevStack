#!/usr/bin/env python3
"""Approved inner design, transparent host boundary and self-contained bootstrap."""
from pathlib import Path
import gzip, hashlib, re, base64
ROOT=Path(__file__).resolve().parents[1]
APPROVED_SHA256='cb44936549a1a2b5fc9176a07790ecc0b7ed56a2268b0142df28e9367fdecfd3'
def build():
    raw=gzip.decompress((ROOT/'design/approved-wsl-01.html.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest()!=APPROVED_SHA256:raise RuntimeError('Approved prototype changed')
    source=raw.decode('utf-8')
    css=re.search(r'<style>(.*?)</style>',source,re.S).group(1)
    icons=re.search(r'<svg[^>]*aria-hidden="true"[^>]*>.*?</svg>',source,re.S).group(0).replace('style="display:none"','class="svg-defs"')
    card=re.search(r'<article\b[^>]*id="widget".*?</article>',source,re.S).group(0)
    replacements={
        'Prototyp widgetu Ubuntu':'Ubuntu','Opcje prototypu':'Opcje widżetu',
        'Odśwież dane demo':'Odśwież dane','Wyłącznie symulacja interfejsu.':'Odczyt tylko przy otwartym panelu.',
        'Przywróć widok':'Zamknij informacje','DANE DEMO':'BRAK DANYCH','demonstracyjnym ':'',
        'Odswiez demonstracyjny stan':'Odśwież stan'}
    for a,b in replacements.items():card=card.replace(a,b)
    for id_ in ['cpuValue','cpuUnit','cpuSmall','ramValue','ramUnit','ramSmall','diskValue','diskUnit','diskSmall','uptimeValue','uptimeUnit','uptimeSmall','syncTime']:
        pattern=r'(<(?:span|i)\b[^>]*id="'+id_+r'"[^>]*>)[^<]*(</(?:span|i)>)'
        card,count=re.subn(pattern,lambda m:m[1]+'—'+m[2],card)
        if count!=1:raise RuntimeError('Reference element missing: '+id_)
    card=re.sub(r'\sstyle="[^"]*"','',card)
    card=card.replace('id="statusTitle">Uruchomione','id="statusTitle">Sprawdzanie…')
    card=card.replace('id="statusSub">Gotowe do pracy.','id="statusSub">Odczytywanie stanu WSL.')
    card=card.replace('id="actionLabel">Zatrzymaj Ubuntu','id="actionLabel">Sprawdzanie…')
    card=card.replace('id="mainAction"','id="mainAction" disabled')
    css+='''\n/* Host boundary only: approved inner geometry remains unchanged. */
.svg-defs{position:absolute;width:0;height:0;overflow:hidden}
html,body{width:100%;height:100%;overflow:hidden;background:transparent!important}
/* One visible edge. Never paint rectangular page background outside the card. */
html[data-host="windows"]{border-radius:8px;overflow:hidden}
html[data-host="windows"] body{border-radius:inherit;overflow:hidden}
html[data-host="windows"] .widget{position:relative;inset:auto;width:100%;height:100%;transform:none!important;box-shadow:none;border-radius:inherit;border-color:transparent}
html[data-host="windows"] #menuButton{visibility:hidden}
@media(max-height:435px){html[data-host="windows"] .widget-header{display:none}}
.widget.paused *{animation-play-state:paused!important}
/* 0.3.4: the ASUS captures show a lighter initial host tile than the WebView
   backing. An 8% white fill maps neutral #202020 to #323232 and keeps its tint.
   This is background alpha only, never opacity on content. It cannot style
   Windows' pre-document spinner or guarantee identical host compositing. */
html[data-host="windows"][data-theme="dark"] .widget{background:rgba(255,255,255,0.08)}
'''
    out=ROOT/'Web';out.mkdir(exist_ok=True)
    # Prevent Windows checkout line endings from invalidating CSP hashes.
    css=css.replace('\r\n','\n').replace('\r','\n')
    js=(out/'wsl.js').read_text(encoding='utf-8').replace('\r\n','\n').replace('\r','\n')
    if '</script' in js.lower() or '</style' in css.lower():raise RuntimeError('Unsafe inline terminator')
    digest=lambda s:base64.b64encode(hashlib.sha256(s.encode('utf-8')).digest()).decode('ascii')
    csp="default-src 'none'; style-src 'sha256-"+digest(css)+"'; script-src 'sha256-"+digest(js)+"'; img-src data: 'self'; connect-src 'self' https://devstack-widgets.invalid; font-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    page='<!doctype html><html lang="pl" data-theme="dark" data-host="windows" data-origin="https://devstack-widgets.invalid"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark light"><meta http-equiv="Content-Security-Policy" content="'+csp+'"><title>Ubuntu</title><style>'+css+'</style></head><body>'+icons+card+'<!--WSL_BOOTSTRAP--><script>'+js+'</script></body></html>'
    for name,text in [('wsl.css',css),('csp.txt',csp),('index.html',page)]:
        (out/name).write_bytes(text.encode('utf-8'))
    (out/'unavailable.html').write_text('<!doctype html><html lang="pl"><meta charset="utf-8"><title>Ubuntu</title><p>Nie mozna zaladowac zasobow widzetu. Otworz panel ponownie.</p></html>',encoding='utf-8')
    print('Approved source hash verified; self-contained HTML with pinned CSP hashes generated.')
if __name__=='__main__':build()
