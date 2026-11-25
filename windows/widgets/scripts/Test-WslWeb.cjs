/* Production HTML/CSP, documented one-argument WebView2 transport and cached recovery. No real WSL. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),zlib=require('node:zlib'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[2]||'wsl-approved-previews');
const nonce='a'.repeat(64);
const reading={cpuPercent:7,memoryUsedBytes:1800000000,memoryTotalBytes:23000000000,diskUsedBytes:12600000000,diskTotalBytes:256000000000,uptimeSeconds:5040};
const base={protocol:1,kind:'state',session:nonce,server:'server-one',revision:1,active:true,canControl:true,phase:'running',cached:false,checkedAt:new Date().toISOString(),metricsStatus:'ok',metrics:reading,confirmUntil:0,confirmation:null,message:''};
const approved=zlib.gunzipSync(fs.readFileSync(path.join(root,'design/approved-wsl-01.html.gz'))).toString();
const html=fs.readFileSync(path.join(root,'Web/index.html'),'utf8'),csp=fs.readFileSync(path.join(root,'Web/csp.txt'),'utf8');
let resourceReads=0;const healthReports=[];
const server=http.createServer((req,res)=>{
 const u=new URL(req.url,'http://localhost');
 if(u.pathname==='/favicon.ico'){res.writeHead(204);return res.end();}
 if(u.pathname==='/approved.html'){res.writeHead(200,{'Content-Type':'text/html'});return res.end(approved);}
 if(u.pathname!=='/index.html'){res.writeHead(404);return res.end();}
 if(req.headers['x-devstack-view']==='state'){
  resourceReads++;healthReports.push(req.headers['x-devstack-bridge']);
  res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
  return res.end(JSON.stringify({...base,revision:1000,transport:'resource',canControl:false}));
 }
 res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':csp,'Cache-Control':'private, no-cache'});
 const boot=u.searchParams.get('cache')==='1'?{...base,cached:true,active:false,canControl:false}:{};
 // The local fixture server replaces only the registered-origin host attribute.
 // Script/style contents and their production CSP hashes remain byte-for-byte.
 const localHtml=html.replace('data-origin="https://devstack-widgets.invalid"','data-origin="http://'+req.headers.host+'"');
 res.end(localHtml.replace('<!--WSL_BOOTSTRAP-->','<script id="wsl-bootstrap" type="application/json">'+JSON.stringify(boot)+'</script>'));
});
const ids=['statusTitle','statusSub','metricCPU','metricRAM','metricDisk','metricUptime','modeLine','mainAction','syncButton'];
const rects=ids=>Object.fromEntries(ids.map(id=>{const n=document.getElementById(id),r=n.getBoundingClientRect(),w=document.getElementById('widget').getBoundingClientRect(),s=getComputedStyle(n);return[id,{x:r.x-w.x,y:r.y-w.y,width:r.width,height:r.height,fontSize:s.fontSize,borderRadius:s.borderRadius}];}));
function mockBridge({state,delay=0,throws=0,drop=0}){
 window.sent=[];window.nativeReceive=null;window.rev=2000;window.mockState=state;window.mockThrows=throws;window.mockDrops=drop;window.postArities=[];
 const attach=()=>{
  window.chrome=window.chrome||{};
  window.chrome.webview={
   addEventListener:(type,cb)=>{if(type==='message')window.nativeReceive=cb;},
   postMessage(raw){
    window.postArities.push(arguments.length);
    if(arguments.length!==1)throw new TypeError('Test contract: WebView2.postMessage(message), not Window.postMessage');
    const msg=JSON.parse(raw);window.sent.push(msg);window.pageClient=msg.client;
    if(window.mockThrows>0){window.mockThrows--;throw new Error('Native bridge not ready');}
    if(msg.kind==='ready'){
     if(window.mockDrops>0){window.mockDrops--;return;}
     setTimeout(()=>window.nativeReceive?.({data:JSON.stringify({...window.mockState,client:window.pageClient,revision:++window.rev})}),10);
    }
   }
  };
 };
 if(delay)setTimeout(attach,delay);else attach();
}
(async()=>{
 fs.mkdirSync(out,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch();let checks=0;const reports=[];
 const reference=await browser.newPage({viewport:{width:1200,height:1000},timezoneId:'Europe/Warsaw'});
 await reference.goto(url+'/approved.html');await reference.evaluate(()=>document.getElementById('widget').style.setProperty('transform','none','important'));
 const expected=await reference.evaluate(rects,ids);await reference.locator('#widget').screenshot({path:path.join(out,'approved-reference.png')});
 for(const theme of ['dark','light']){
  const page=await browser.newPage({viewport:{width:300,height:462},deviceScaleFactor:2,timezoneId:'Europe/Warsaw',colorScheme:theme});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.addInitScript(mockBridge,{state:base});
  await page.goto(url+'/index.html?session='+nonce+'&theme='+theme+'&auth=true');
  await page.locator('#mainAction:not([disabled])').waitFor({state:'visible',timeout:10000});
  assert.ok(await page.evaluate(()=>window.postArities.length>0&&window.postArities.every(n=>n===1)));checks++;
  const publish=async extra=>page.evaluate(s=>{window.mockState={...window.mockState,...s};window.nativeReceive({data:JSON.stringify({...window.mockState,client:window.pageClient,revision:++window.rev})});},extra);
  await publish({checkedAt:'2026-11-24T20:48:12Z'});
  if(theme==='dark'){
   const actual=await page.evaluate(rects,ids);
   for(const id of ids){for(const dimension of ['x','y','width','height']){assert.ok(Math.abs(actual[id][dimension]-expected[id][dimension])<=1.1,id+' '+dimension+' differs from approved prototype');checks++;}assert.equal(actual[id].fontSize,expected[id].fontSize);assert.equal(actual[id].borderRadius,expected[id].borderRadius);checks+=2;}
   reports.push({comparison:'approved inner geometry; no design change in transport fix',maxAllowedDeltaPx:1.1,elements:ids.length});
  }
  const backgrounds=await page.evaluate(()=>[getComputedStyle(document.documentElement).backgroundColor,getComputedStyle(document.body).backgroundColor,getComputedStyle(document.getElementById('widget')).borderTopColor]);
  assert.deepEqual(backgrounds,['rgba(0, 0, 0, 0)','rgba(0, 0, 0, 0)','rgba(0, 0, 0, 0)']);checks++;
  for(const phase of ['running','offline','error','starting','stopping','confirm']){
   await publish({phase,confirmUntil:Date.now()+30000,confirmation:phase==='confirm'?'stop-token':null});
   const footer=await page.locator('.widget-footer').boundingBox();assert.ok(footer.y+footer.height<=450.1);checks++;
   const action=await page.locator('#mainAction').boundingBox();assert.ok(action.y+action.height<footer.y);checks++;
   await page.screenshot({path:path.join(out,phase+'-'+theme+'.png'),animations:'disabled',omitBackground:true});
  }
  const lastAction=()=>page.evaluate(()=>window.sent.filter(m=>m.kind==='action').at(-1));
  await publish({phase:'running'});await page.locator('#mainAction').click();assert.equal((await lastAction()).verb,'stop-ask');checks++;
  await publish({phase:'confirm',confirmation:'stop-token',confirmUntil:Date.now()+30000});await page.locator('#cancelButton').click();assert.equal((await lastAction()).verb,'stop-cancel');checks++;
  await publish({phase:'confirm',confirmation:'stop-token',confirmUntil:Date.now()+30000});await page.locator('#mainAction').click();assert.equal((await lastAction()).confirmation,'stop-token');checks++;
  await publish({phase:'offline',metrics:null});await page.locator('#mainAction').click();assert.equal((await lastAction()).verb,'start');checks++;
  await publish({phase:'error',metrics:null});await page.locator('#mainAction').click();assert.equal((await lastAction()).verb,'refresh');checks++;
  await publish({phase:'running',metrics:null,metricsStatus:'not-installed'});assert.equal(await page.locator('#ramValue').innerText(),'—');checks++;
  await page.locator('#metricRAM').click();assert.equal(await page.locator('#metricPopover').isVisible(),true);checks++;
  await page.locator('#detailTitle').click();assert.equal(await page.locator('#metricPopover').isVisible(),true);checks++;
  await page.locator('#statusTitle').click();assert.equal(await page.locator('#metricPopover').isVisible(),false);checks++;
  await page.locator('#metricCPU').click();assert.equal(await page.locator('#metricPopover').isVisible(),true);checks++;
  await page.keyboard.press('Escape');assert.equal(await page.locator('#metricPopover').isVisible(),false);checks++;
  await page.locator('#metricRAM').click();await page.locator('#closeDetail').click();assert.equal(await page.locator('#metricPopover').isVisible(),false);checks++;
  await publish({phase:'running',canControl:false});assert.equal(await page.locator('#mainAction').isDisabled(),true);checks++;
  await publish({phase:'running',active:false});assert.equal(await page.locator('#mainAction').isDisabled(),true);assert.equal(await page.locator('#syncButton').isDisabled(),true);checks+=2;
  const beforeReads=resourceReads;await page.waitForTimeout(1800);assert.equal(resourceReads,beforeReads);checks++;
  await publish({phase:'confirm',active:true,canControl:true,confirmation:'expired',confirmUntil:Date.now()-1});assert.equal(await page.locator('#cancelButton').isHidden(),true);checks++;
  const before=await page.locator('#statusTitle').innerText();await page.evaluate(s=>window.nativeReceive({data:JSON.stringify(s)}),{...base,session:'invalid',phase:'offline',revision:99999});assert.equal(await page.locator('#statusTitle').innerText(),before);checks++;
  await page.evaluate(s=>window.nativeReceive({data:JSON.stringify({...s,client:window.pageClient})}),{...base,server:'server-two',revision:1,phase:'offline'});
  assert.equal(await page.locator('#statusTitle').innerText(),'Wyłączone');checks++;
  await page.evaluate(s=>window.nativeReceive({data:JSON.stringify({...s,client:window.pageClient})}),{...base,server:'server-one',revision:99999});
  assert.equal(await page.locator('#statusTitle').innerText(),'Wyłączone');checks++;
  for(const height of [422,462,600]){await page.setViewportSize({width:300,height});const bottom=await page.locator('.widget-footer').boundingBox();assert.ok(bottom.y+bottom.height<=height-10);checks++;}
  assert.deepEqual(errors,[]);checks++;
  assert.ok(await page.evaluate(()=>window.postArities.every(n=>n===1)));checks++;
  await page.close();
 }
 for(const scenario of [{name:'late-bridge',delay:500},{name:'throwing-bridge',throws:2},{name:'lost-hello',drop:2}]){
  const page=await browser.newPage({viewport:{width:300,height:462}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(mockBridge,{state:base,...scenario});await page.goto(url+'/index.html?session='+nonce+'&auth=true');
  await page.locator('#mainAction:not([disabled])').waitFor({state:'visible',timeout:10000});
  assert.equal(await page.locator('#statusTitle').innerText(),'Uruchomione');assert.equal(await page.locator('#cpuValue').innerText(),'7');checks+=2;
  assert.deepEqual(errors,[]);checks++;
  assert.ok(await page.evaluate(()=>window.postArities.every(n=>n===1)));checks++;
  reports.push({startup:scenario.name,result:'recovered'});await page.close();
 }
 const noBridge=await browser.newPage({viewport:{width:300,height:462}});
 const beforeHealth=healthReports.length;
 await noBridge.goto(url+'/index.html?session='+nonce+'&auth=true');await noBridge.waitForTimeout(250);
 assert.equal(await noBridge.locator('#cpuValue').innerText(),'7');checks++;
 assert.equal(await noBridge.locator('#mainAction').isDisabled(),true);checks++;
 assert.ok(healthReports.slice(beforeHealth).includes('missing'));checks++;
 reports.push({startup:'missing-native-bridge',result:'resource data only; controls remain disabled; diagnostic recorded'});
 await noBridge.close();
 const cached=await browser.newPage({viewport:{width:300,height:462}});
 await cached.route('**/index.html*',route=>{if(route.request().headers()['x-devstack-view']==='state')return route.abort();return route.continue();});
 await cached.goto(url+'/index.html?session='+nonce+'&cache=1');
 assert.equal(await cached.locator('#statusTitle').innerText(),'Uruchomione');assert.equal(await cached.locator('#mainAction').isDisabled(),true);checks+=2;
 assert.equal(await cached.locator('.data-demo').innerText(),'OSTATNI ODCZYT');checks++;
 await cached.screenshot({path:path.join(out,'cached-before-bridge.png'),omitBackground:true});await cached.close();
 await reference.close();await browser.close();await new Promise(resolve=>server.close(resolve));
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,reports,postMessageContract:'one-argument mock based on WebView2 API; not proof of host root cause',actualWindowsHostTested:false},null,2));
 console.log('PASS: '+checks+' production CSP/UI/recovery assertions. Actual Windows host still requires device verification.');
})().catch(error=>{console.error(error);server.close();process.exit(1);});
