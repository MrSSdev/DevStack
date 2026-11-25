/* Exact approved 03 layout with production CSP and native bridge fixtures; no real service commands. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),zlib=require('node:zlib'),assert=require('node:assert/strict');
const{chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[2]||'services-approved-previews');
const nonce='b'.repeat(64);
const services=['nginx','php','mariadb','redis'].map((id,i)=>({id,reachable:true,latencyMs:[12,24,3,2][i],memoryBytes:[18,64,192,12][i]*1e6,cpuPercent:[.2,.4,.1,.1][i],unitActive:true}));
const base={protocol:1,kind:'state',session:nonce,server:'services-one',revision:1,active:true,canControl:true,canNavigate:true,cached:false,phase:'running',integration:'ok',services,projectCount:3,checkedAt:'2026-11-25T00:29:59+01:00',busy:null,message:'',confirmation:null,confirmUntil:0,confirmService:null};
const html=fs.readFileSync(path.join(root,'ServicesWeb/index.html'),'utf8'),csp=fs.readFileSync(path.join(root,'ServicesWeb/csp.txt'),'utf8');
const approved=zlib.gunzipSync(fs.readFileSync(path.join(root,'design/approved-devstack-03.html.gz'))).toString();
let reads=0;
const server=http.createServer((req,res)=>{
 const u=new URL(req.url,'http://localhost');
 if(u.pathname==='/favicon.ico'){res.writeHead(204);return res.end();}
 if(u.pathname==='/approved'){res.writeHead(200,{'Content-Type':'text/html'});return res.end(approved);}
 if(u.pathname!=='/index.html'){res.writeHead(404);return res.end();}
 if(req.headers['x-devstack-view']==='state'){reads++;res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify({...base,revision:1000,transport:'resource',canControl:false,canNavigate:false}));}
 res.writeHead(200,{'Content-Type':'text/html','Content-Security-Policy':csp});
 res.end(html.replace('data-origin="https://devstack-services.invalid"','data-origin="http://'+req.headers.host+'"').replace('<!--DEVSTACK_BOOTSTRAP-->',''));
});
function mock({state,delay=0}){
 window.sent=[];window.receive=null;window.rev=2000;window.mockState=state;
 function attach(){window.chrome=window.chrome||{};window.chrome.webview={addEventListener:(t,cb)=>{if(t==='message')window.receive=cb;},postMessage(raw){if(arguments.length!==1)throw Error('arity');const m=JSON.parse(raw);window.sent.push(m);window.client=m.client;if(m.kind==='ready')setTimeout(()=>window.receive({data:JSON.stringify({...window.mockState,client:window.client,revision:++window.rev})}),10);}};}
 if(delay)setTimeout(attach,delay);else attach();
}
const ids=['statusTitle','statusSub','service-nginx','service-php','service-mariadb','service-redis','projectCountLine','addProjectButton','projectsButton','syncButton'];
const rects=ids=>Object.fromEntries(ids.map(id=>{let r=document.getElementById(id).getBoundingClientRect(),w=document.getElementById('widget').getBoundingClientRect(),s=getComputedStyle(document.getElementById(id));return[id,{x:r.x-w.x,y:r.y-w.y,width:r.width,height:r.height,font:s.fontSize,radius:s.borderRadius}];}));
(async()=>{
 fs.mkdirSync(out,{recursive:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox']}:{});let checks=0;
 const ref=await browser.newPage({viewport:{width:1200,height:1000}});await ref.goto(url+'/approved');await ref.evaluate(()=>document.getElementById('hostSurface').style.transform='none');const expected=await ref.evaluate(rects,ids);
 for(const theme of ['dark','light']){
  const page=await browser.newPage({viewport:{width:300,height:462},deviceScaleFactor:2,colorScheme:theme,timezoneId:'Europe/Warsaw'});const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.clock.install();
  await page.addInitScript(mock,{state:base});await page.goto(url+'/index.html?session='+nonce+'&theme='+theme+'&auth=true');
  await page.locator('#projectsButton[aria-disabled="false"]').waitFor();
  const publish=extra=>page.evaluate(extra=>{window.mockState={...window.mockState,...extra};window.receive({data:JSON.stringify({...window.mockState,client:window.client,revision:++window.rev})});},extra);
  const actual=await page.evaluate(rects,ids);for(const id of ids){for(const prop of ['x','y','width','height']){assert.ok(Math.abs(actual[id][prop]-expected[id][prop])<=1.1,id+' '+prop);checks++;}assert.equal(actual[id].font,expected[id].font);assert.equal(actual[id].radius,expected[id].radius);checks+=2;}
  const bg=await page.evaluate(()=>getComputedStyle(document.getElementById('widget')).backgroundColor);if(theme==='dark'){assert.equal(bg,'rgba(0, 0, 0, 0)');checks++;}
  assert.equal(await page.locator('#latency-nginx').innerText(),'12 ms');assert.equal(await page.locator('#ram-nginx').innerText(),'18 MB');checks+=2;
  await page.screenshot({path:path.join(out,'running-'+theme+'.png'),omitBackground:true});
  await page.locator('#service-nginx').click();assert.equal(await page.locator('#servicePopover').isVisible(),true);assert.equal(await page.locator('#restartServiceButton').isEnabled(),true);checks+=2;
  await page.screenshot({path:path.join(out,'details-'+theme+'.png'),omitBackground:true});
  const last=()=>page.evaluate(()=>window.sent.filter(m=>m.kind==='action').at(-1));
  await page.locator('#restartServiceButton').click();assert.equal((await last()).verb,'restart-ask');assert.equal((await last()).service,'nginx');checks+=2;
  await publish({confirmService:'nginx',confirmation:'one-use',confirmUntil:Date.now()+30000});await page.locator('#cancelServiceRestart').click();assert.equal((await last()).verb,'restart-cancel');checks++;
  await publish({confirmService:'nginx',confirmation:'one-use',confirmUntil:Date.now()+30000});await page.locator('#restartServiceButton').click();assert.equal((await last()).verb,'restart-confirm');assert.equal((await last()).confirmation,'one-use');checks+=2;
  assert.equal(await page.locator('#servicePopover').isVisible(),false);checks++;
  assert.match(await page.locator('#widgetMessage').innerText(),/Nginx: ponowne uruchamianie/);checks++;
  await publish({busy:null,confirmService:null,confirmation:null,restartStatus:{service:'nginx',phase:'verifying',expiresAt:0}});
  assert.match(await page.locator('#widgetMessage').innerText(),/Nginx: restart zakończony. Weryfikowanie/);checks++;
  const bounds=await page.evaluate(()=>{const r=id=>document.getElementById(id).getBoundingClientRect();return {inline:r('widgetToast').top>=r('projectCountLine').bottom,above:r('widgetToast').bottom<=r('addProjectButton').top,position:getComputedStyle(document.getElementById('widgetToast')).position};});
  assert.ok(bounds.inline&&bounds.above&&bounds.position!=='absolute');checks++;
  await page.screenshot({path:path.join(out,'verifying-'+theme+'.png'),omitBackground:true});
  const success={service:'nginx',phase:'success',expiresAt:await page.evaluate(()=>Date.now()+30000)};
  await publish({restartStatus:success});
  assert.match(await page.locator('#widgetMessage').innerText(),/Nginx: zweryfikowano/);checks++;
  await page.screenshot({path:path.join(out,'restart-success-'+theme+'.png'),omitBackground:true});
  await page.clock.fastForward(31000);assert.equal(await page.locator('#widgetToast').isVisible(),false);checks++;
  await publish({restartStatus:success});assert.equal(await page.locator('#widgetToast').isVisible(),false);checks++;
  await publish({restartStatus:{service:'php',phase:'error',expiresAt:0}});assert.match(await page.locator('#widgetMessage').innerText(),/PHP-FPM: błąd weryfikacji/);checks++;
  await page.clock.fastForward(31000);assert.equal(await page.locator('#widgetToast').isVisible(),true);checks++;
  await page.screenshot({path:path.join(out,'restart-error-'+theme+'.png'),omitBackground:true});
  const recovered={service:'php',phase:'recovered',expiresAt:await page.evaluate(()=>Date.now()+30000)};
  await publish({restartStatus:recovered});assert.match(await page.locator('#widgetMessage').innerText(),/PHP-FPM: usługa ponownie dostępna/);checks++;
  assert.equal(await page.locator('#widgetToast').getAttribute('data-kind'),'success');checks++;
  await page.screenshot({path:path.join(out,'restart-recovered-'+theme+'.png'),omitBackground:true});
  await page.clock.fastForward(31000);assert.equal(await page.locator('#widgetToast').isVisible(),false);checks++;
  await publish({restartStatus:recovered});assert.equal(await page.locator('#widgetToast').isVisible(),false);checks++;
  for(const phase of ['available-unconfirmed','available-not-run']){
   await publish({restartStatus:{service:'redis',phase,expiresAt:await page.evaluate(()=>Date.now()+30000)}});
   assert.match(await page.locator('#widgetMessage').innerText(),/Redis: usługa działa; restart nie/);checks++;
   assert.equal(await page.locator('#widgetToast').getAttribute('data-kind'),'info');checks++;
   await page.clock.fastForward(31000);assert.equal(await page.locator('#widgetToast').isVisible(),false);checks++;
  }
  await publish({restartStatus:null});assert.equal(await page.locator('#widgetToast').isVisible(),false);checks++;
  await page.locator('#projectsButton').click();assert.equal((await last()).verb,'open-projects');checks++;
  await page.locator('#addProjectButton').click();assert.equal((await last()).verb,'open-new');checks++;
  for(const phase of ['offline','error','checking']){await publish({phase,canControl:false,canNavigate:false,services:[],projectCount:null});assert.equal(await page.locator('#projectSummary').innerText(),'Łącznie projektów: —');assert.equal(await page.locator('#projectsButton').getAttribute('aria-disabled'),'true');checks+=2;await page.screenshot({path:path.join(out,phase+'-'+theme+'.png'),omitBackground:true});}
  await publish({phase:'running',services,integration:'not-installed'});await page.locator('#service-nginx').click();assert.equal(await page.locator('#restartServiceButton').isDisabled(),true);assert.equal(await page.locator('#restartPrompt').isVisible(),true);checks+=2;
  await publish({active:false});const before=reads;await page.waitForTimeout(1800);assert.equal(reads,before);checks++;
  assert.deepEqual(errors,[]);checks++;await page.close();
 }
 const late=await browser.newPage({viewport:{width:300,height:462}});await late.addInitScript(mock,{state:base,delay:600});await late.goto(url+'/index.html?session='+nonce+'&auth=true');await late.locator('#projectsButton[aria-disabled="false"]').waitFor();checks++;await late.close();
 await ref.close();await browser.close();await new Promise(r=>server.close(r));fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,approvedSource:'DevStack-widget-prototyp-03.html',nativeTransportMocked:true,actualWindowsHostTested:false},null,2));console.log('PASS: '+checks+' DevStack production-page checks.');
})().catch(e=>{console.error(e);server.close();process.exit(1);});
