/* Real production HTML/CSS/JS, a mocked native message transport and an approved-source comparison. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),zlib=require('node:zlib'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[2]||'wsl-approved-previews');
const nonce='a'.repeat(64),received=[];
const reading={cpuPercent:7,memoryUsedBytes:1800000000,memoryTotalBytes:23000000000,diskUsedBytes:12600000000,diskTotalBytes:256000000000,uptimeSeconds:5040};
const base={protocol:1,kind:'state',session:nonce,revision:1,active:true,canControl:true,phase:'running',checkedAt:'2026-11-24T20:48:12Z',metricsStatus:'ok',metrics:reading,confirmUntil:0,confirmation:null,message:''};
const approved=zlib.gunzipSync(fs.readFileSync(path.join(root,'design/approved-wsl-01.html.gz'))).toString();
const files={'/index.html':['index.html','text/html'],'/wsl.css':['wsl.css','text/css'],'/wsl.js':['wsl.js','text/javascript']};
const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://localhost');if(u.pathname==='/favicon.ico'){res.writeHead(204);return res.end();}if(u.pathname==='/approved.html'){res.writeHead(200,{'Content-Type':'text/html'});return res.end(approved);}const hit=files[u.pathname];if(!hit){res.writeHead(404);return res.end();}res.writeHead(200,{'Content-Type':hit[1]});res.end(fs.readFileSync(path.join(root,'Web',hit[0])));});
const ids=['statusTitle','statusSub','metricCPU','metricRAM','metricDisk','metricUptime','modeLine','mainAction','syncButton'];
const rects=ids=>Object.fromEntries(ids.map(id=>{const node=document.getElementById(id),r=node.getBoundingClientRect(),w=document.getElementById('widget').getBoundingClientRect(),s=getComputedStyle(node);return[id,{x:r.x-w.x,y:r.y-w.y,width:r.width,height:r.height,fontSize:s.fontSize,borderRadius:s.borderRadius}];}));
(async()=>{
 fs.mkdirSync(out,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch();let checks=0;const reports=[];
 const reference=await browser.newPage({viewport:{width:1200,height:1000},timezoneId:'Europe/Warsaw'});
 await reference.goto(url+'/approved.html');await reference.evaluate(()=>document.getElementById('widget').style.setProperty('transform','none','important'));
 const expected=await reference.evaluate(rects,ids);await reference.locator('#widget').screenshot({path:path.join(out,'approved-reference.png')});
 for(const theme of ['dark','light']){
  const page=await browser.newPage({viewport:{width:300,height:462},deviceScaleFactor:2,timezoneId:'Europe/Warsaw',colorScheme:theme});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.addInitScript(()=>{window.sent=[];window.nativeReceive=null;window.chrome={webview:{postMessage:raw=>window.sent.push(JSON.parse(raw)),addEventListener:(kind,cb)=>{window.nativeReceive=cb;}}};});
  await page.goto(url+'/index.html?session='+nonce+'&theme='+theme+'&auth=true');
  assert.equal(await page.locator('#cpuValue').innerText(),'\u2014');checks++;
  let revision=0;
  const publish=async extra=>{await page.evaluate(s=>window.nativeReceive({data:JSON.stringify(s)}),{...base,...extra,revision:++revision});};
  await publish({});
  if(theme==='dark'){
   const actual=await page.evaluate(rects,ids);
   for(const id of ids){for(const dimension of ['x','y','width','height']){assert.ok(Math.abs(actual[id][dimension]-expected[id][dimension])<=1.1,id+' '+dimension+' differs from approved prototype');checks++;}assert.equal(actual[id].fontSize,expected[id].fontSize);assert.equal(actual[id].borderRadius,expected[id].borderRadius);checks+=2;}
   reports.push({comparison:'approved prototype inner geometry',maxAllowedDeltaPx:1.1,elements:ids.length});
  }
  for(const phase of ['running','offline','error','starting','stopping','confirm']){
   await publish({phase,confirmUntil:Date.now()+30000,confirmation:phase==='confirm'?'stop-token':null});
   const footer=await page.locator('.widget-footer').boundingBox();assert.ok(footer.y+footer.height<=450.1);checks++;
   const action=await page.locator('#mainAction').boundingBox();assert.ok(action.y+action.height<footer.y);checks++;
   await page.screenshot({path:path.join(out,phase+'-'+theme+'.png'),animations:'disabled'});
  }
  await publish({phase:'running'});await page.locator('#mainAction').click();assert.equal(await page.evaluate(()=>window.sent.at(-1).verb),'stop-ask');checks++;
  await publish({phase:'confirm',confirmation:'stop-token',confirmUntil:Date.now()+30000});
  await page.locator('#cancelButton').click();assert.equal(await page.evaluate(()=>window.sent.at(-1).verb),'stop-cancel');checks++;
  await publish({phase:'confirm',confirmation:'stop-token',confirmUntil:Date.now()+30000});
  await page.locator('#mainAction').click();assert.equal(await page.evaluate(()=>window.sent.at(-1).confirmation),'stop-token');checks++;
  await publish({phase:'offline',metrics:null});await page.locator('#mainAction').click();assert.equal(await page.evaluate(()=>window.sent.at(-1).verb),'start');checks++;
  await publish({phase:'error',metrics:null});await page.locator('#mainAction').click();assert.equal(await page.evaluate(()=>window.sent.at(-1).verb),'refresh');checks++;
  await publish({phase:'running',metrics:null,metricsStatus:'not-installed'});assert.equal(await page.locator('#ramValue').innerText(),'\u2014');checks++;
  await page.locator('#metricRAM').click();assert.equal(await page.locator('#metricPopover').isVisible(),true);checks++;
  await page.screenshot({path:path.join(out,'metrics-unavailable-'+theme+'.png')});await page.keyboard.press('Escape');assert.equal(await page.locator('#metricPopover').isVisible(),false);checks++;
  await publish({phase:'running',canControl:false});assert.equal(await page.locator('#mainAction').isDisabled(),true);checks++;
  await publish({phase:'running',active:false});assert.equal(await page.locator('#mainAction').isDisabled(),true);assert.equal(await page.locator('#syncButton').isDisabled(),true);checks+=2;
  await publish({phase:'confirm',confirmation:'expired',confirmUntil:Date.now()-1});assert.equal(await page.locator('#cancelButton').isHidden(),true);checks++;
  const before=await page.locator('#statusTitle').innerText();await page.evaluate(s=>window.nativeReceive({data:JSON.stringify(s)}),{...base,session:'invalid',phase:'offline',revision:9999});assert.equal(await page.locator('#statusTitle').innerText(),before);checks++;
  assert.deepEqual(errors,[]);checks++;
  for(const height of [422,462,600]){await page.setViewportSize({width:300,height});await publish({});const bottom=await page.locator('.widget-footer').boundingBox();assert.ok(bottom.y+bottom.height<=height-10);checks++;}
  await page.close();
 }
 await reference.close();await browser.close();await new Promise(resolve=>server.close(resolve));
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,reports,actualWindowsHostTested:false},null,2));
 console.log('PASS: '+checks+' production UI/interaction assertions; approved CSS geometry matched. Real Windows host still needs device validation.');
})().catch(error=>{console.error(error);server.close();process.exit(1);});
