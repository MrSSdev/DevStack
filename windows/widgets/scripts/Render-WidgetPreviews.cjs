'use strict';
// Render the exact C# fixture JSON with Microsoft's Adaptive Cards JS renderer.
// Bounds tests are a regression check, NOT a substitute for the Windows host.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const sdk = path.join(path.dirname(require.resolve('adaptivecards/package.json')), 'dist/adaptivecards.min.js');
const input = path.resolve(process.argv[2] || 'widgets-previews');
const output = path.join(input, 'rendered');
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = [];
  try {
    const files = fs.readdirSync(input).filter(n => n.endsWith('.json'));
    if (!files.length) throw new Error('No compiled C# card fixtures were exported');
    for (const theme of ['dark', 'light']) {
      const dark = theme === 'dark';
      const page = await browser.newPage({ viewport: { width: 340, height: 504 }, deviceScaleFactor: 1.5 });
      for (const file of files) {
        const fixture = JSON.parse(fs.readFileSync(path.join(input, file), 'utf8'));
        // Fixture represents an authenticated Windows host. Do not remove
        // the authentication guard from the production payload.
        const clean = v => {
          if (Array.isArray(v)) return v.map(clean);
          if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).filter(([k]) => k !== '$when').map(([k,x]) => [k,clean(x)]));
          return v;
        };
        await page.setContent(`<html lang="pl"><meta charset="utf-8"><style>
          *{box-sizing:border-box} body{margin:0;padding:20px;background:${dark?'#202124':'#e8ebef'};font-family:'Segoe UI',sans-serif}
          #host{width:300px;height:462px;position:relative;border-radius:8px;background:${dark?'#2d2d2d':'#fff'};box-shadow:0 2px 5px #0002}
          #header{position:absolute;top:15px;left:16px;right:16px;height:18px;display:flex;justify-content:space-between;color:${dark?'#ededed':'#202124'};font-size:14px;z-index:2}
          .ac-adaptiveCard{height:100%;padding:48px 16px 16px!important;border:0!important;border-radius:8px}
          .ac-pushButton{font-family:inherit;font-size:14px;min-height:32px;padding:5px 10px;border:1px solid ${dark?'#ffffff12':'#00000018'};border-radius:5px;background:${dark?'#ffffff0c':'#ffffff'};color:inherit;white-space:normal}
          .ac-pushButton.primary{background:${dark?'#a9c3da':'#345d7a'};color:${dark?'#172532':'#fff'}}
          .ac-pushButton.style-destructive{color:${dark?'#efb2b9':'#a12738'}}
          p{margin:0} button:focus-visible{outline:2px solid #709bbb}
          </style><div id="host"><div id="header"><span>${file.startsWith('ubuntu')?'Ubuntu':'DevStack'}</span><span>···</span></div><div id="mount" style="height:100%"></div></div></html>`);
        await page.addScriptTag({ path: sdk });
        await page.evaluate(({payload,dark}) => {
          const AC = window.AdaptiveCards;
          AC.AdaptiveCard.onProcessMarkdown = (text, result) => {
            result.outputHtml = '<p>'+text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</p>';
            result.didProcess = true;
          };
          const fg = dark?'#efefef':'#24272c', muted = dark?'#bcbfc5':'#61666d';
          const colors = { default:{default:fg,subtle:muted},good:{default:dark?'#91bea4':'#326f50',subtle:muted},attention:{default:dark?'#e6a6ae':'#9e3342',subtle:muted},warning:{default:dark?'#cdb78c':'#785f31',subtle:muted},accent:{default:dark?'#b1c7dc':'#375e7d',subtle:muted} };
          const c = new AC.AdaptiveCard();
          c.hostConfig = new AC.HostConfig({fontFamily:'Segoe UI',supportsInteractivity:true,
            spacing:{small:4,default:8,medium:12,large:16,extraLarge:24,padding:16},
            fontSizes:{small:12,default:14,medium:18,large:20,extraLarge:28},
            fontWeights:{lighter:400,default:400,bolder:600},
            containerStyles:{default:{backgroundColor:dark?'#2d2d2d':'#ffffff',foregroundColors:colors},emphasis:{backgroundColor:dark?'#383838':'#f0f2f5',foregroundColors:colors}},
            actions:{maxActions:4,spacing:12,buttonSpacing:8,actionsOrientation:'horizontal',actionAlignment:'stretch'}
          });
          c.parse(payload);
          const validation = c.validateProperties();
          window.validationEvents = validation && validation.validationEvents ? validation.validationEvents : [];
          window.previewCard = c;
          const rendered = c.render();
          rendered.style.height='100%';
          document.getElementById('mount').appendChild(rendered);
        }, { payload: clean(fixture), dark });
        await page.waitForFunction(() => Array.from(document.images).every(i => i.complete && i.naturalWidth > 0));
        const metrics = await page.evaluate(() => {
          const root = document.querySelector('.ac-adaptiveCard');
          const r = root.getBoundingClientRect();
          const footer = window.previewCard.getElementById('sync-row').renderedElement.getBoundingClientRect();
          const overflow = [...root.querySelectorAll('p,button,img')].filter(e => {
            const b=e.getBoundingClientRect();
            return b.height>0 && (b.bottom>r.bottom-15 || b.top<r.top+47 || b.left<r.left+15 || b.right>r.right-15);
          }).map(e => ({text:e.textContent,tag:e.tagName,rect:{width:e.getBoundingClientRect().width,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom}}));
          return {footerGap:Math.round((r.bottom-footer.bottom)*100)/100,overflow,rootHeight:r.height};
        });
        const name=file.replace('.json','')+'-'+theme;
        report.push({name,...metrics});
        await page.locator('#host').screenshot({path:path.join(output,name+'.png')});
        console.log(name+': footerGap='+metrics.footerGap+'px, overflow='+metrics.overflow.length);
      }
      await page.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output,'layout-report.json'),JSON.stringify(report,null,2));
  const failed=report.filter(r=>r.overflow.length || Math.abs(r.footerGap-16)>2);
  if(failed.length) { console.error(JSON.stringify(failed,null,2)); throw new Error(failed.length+' fixture(s) overflow or footer not anchored'); }
  console.log('PASS: '+report.length+' renderer layouts. Physical Windows QA is still required.');
})().catch(error => { console.error(error); process.exitCode=1; });
