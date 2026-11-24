#!/usr/bin/env python3
"""Extract the approved card verbatim; replace only demo wiring and host chrome."""
from pathlib import Path
import gzip, hashlib, re

ROOT = Path(__file__).resolve().parents[1]
APPROVED_SHA256 = 'cb44936549a1a2b5fc9176a07790ecc0b7ed56a2268b0142df28e9367fdecfd3'

def build():
    raw = gzip.decompress((ROOT / 'design/approved-wsl-01.html.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest() != APPROVED_SHA256:
        raise RuntimeError('Approved prototype changed; explicit design approval is required')
    source = raw.decode('utf-8')
    css = re.search(r'<style>(.*?)</style>', source, re.S).group(1)
    icons = re.search(r'<svg[^>]*aria-hidden="true"[^>]*>.*?</svg>', source, re.S).group(0)
    icons = icons.replace('style="display:none"', 'class="svg-defs"')
    card = re.search(r'<article\b[^>]*id="widget".*?</article>', source, re.S).group(0)
    card = card.replace('Prototyp widgetu Ubuntu', 'Ubuntu')
    card = card.replace('Opcje prototypu', 'Opcje wid\u017cetu')
    card = card.replace('Od\u015bwie\u017c dane demo', 'Od\u015bwie\u017c dane')
    card = card.replace('Wy\u0142\u0105cznie symulacja interfejsu.', 'Odczyt tylko przy otwartym panelu.')
    card = card.replace('Przywr\u00f3\u0107 widok', 'Zamknij informacje')
    card = card.replace('DANE DEMO', 'BRAK DANYCH')
    card = card.replace('demonstracyjnym ', '').replace('Odswiez demonstracyjny stan', 'Od\u015bwie\u017c stan')
    # A cold load must never flash the reference's fictional online values.
    for id_ in ['cpuValue','cpuUnit','cpuSmall','ramValue','ramUnit','ramSmall',
                'diskValue','diskUnit','diskSmall','uptimeValue','uptimeUnit','uptimeSmall','syncTime']:
        pattern = r'(<(?:span|i)\b[^>]*id="' + id_ + r'"[^>]*>)[^<]*(</(?:span|i)>)'
        card, count = re.subn(pattern, lambda m:m[1]+'\u2014'+m[2], card)
        if count != 1: raise RuntimeError('Reference element missing: '+id_)
    card = re.sub(r'(<i[^>]*id="(?:cpu|ram|disk)Bar"[^>]*style=")[^"]*',r'\g<1>width:0%',card)
    card = re.sub(r'\sstyle="[^"]*"', '', card)
    card = card.replace('id="statusTitle">Uruchomione', 'id="statusTitle">Sprawdzanie\u2026')
    card = card.replace('id="statusSub">Gotowe do pracy.', 'id="statusSub">Odczytywanie stanu WSL.')
    card = card.replace('id="actionLabel">Zatrzymaj Ubuntu', 'id="actionLabel">Sprawdzanie\u2026')
    card = card.replace('id="mainAction"', 'id="mainAction" disabled')
    out=ROOT/'Web';out.mkdir(exist_ok=True)
    (out/'wsl.css').write_text(css + '''\n/* Host adapter only. The approved card's tokens and inner layout are unchanged. */
.svg-defs{position:absolute;width:0;height:0;overflow:hidden}
html,body{width:100%;height:100%;overflow:hidden;background:var(--card)}
.widget{position:relative;inset:auto;width:100%;height:100%;transform:none!important;box-shadow:none}
/* Windows owns the clickable ellipsis overlay, including Move and Remove. */
html[data-host="windows"] #menuButton{visibility:hidden}
@media(max-height:435px){html[data-host="windows"] .widget-header{display:none}}
.widget.paused *{animation-play-state:paused!important}
''',encoding='utf-8')
    csp="default-src 'none'; style-src 'self'; script-src 'self'; img-src data: 'self'; font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
    page='<!doctype html><html lang="pl" data-theme="dark" data-host="windows"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark light"><meta http-equiv="Content-Security-Policy" content="'+csp+'"><title>Ubuntu</title><link rel="stylesheet" href="wsl.css"></head><body>'+icons+card+'<script src="wsl.js"></script></body></html>'
    (out/'index.html').write_text(page,encoding='utf-8')
    (out/'unavailable.html').write_text('<!doctype html><html lang="pl"><meta charset="utf-8"><title>Ubuntu</title><p>Nie mo\u017cna za\u0142adowa\u0107 zasob\u00f3w wid\u017cetu. Otw\u00f3rz panel ponownie.</p></html>',encoding='utf-8')
    print('Approved HTML SHA-256:',APPROVED_SHA256)
    print('Generated local Web/index.html and exact approved CSS; no demo runtime.')
if __name__=='__main__':build()
