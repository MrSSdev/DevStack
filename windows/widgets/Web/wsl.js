'use strict';
// Read-only provider fallback only while visible; no demo readings or shell commands.
(() => {
 const $ = id => document.getElementById(id);
 const query = new URLSearchParams(location.search);
 let boot=null;try{boot=JSON.parse($('wsl-bootstrap')?.textContent||'null');}catch{}
 const session = query.get('session') || boot?.session || '';
 const targetOrigin=document.documentElement.dataset.origin||location.origin;
 const sourceUrl=boot?.documentUri||location.href;
 let bridge=null,subscribed=null,connected=false,closedByHost=false,readyAttempts=0,retryTimer=null,fallbackTimer=null,requestAbort=null;
 let bridgeHealth='not-attempted';
 let currentServer=null,lastNativeAt=0;const retiredServers=new Set();
 const cacheKey='devstack-wsl-view-1-'+session;
 // client is a correlation ID, never an authentication or confirmation token.
 const client=(()=>{try{const c=globalThis.crypto;if(typeof c?.randomUUID==='function')return c.randomUUID();if(c?.getRandomValues)return Array.from(c.getRandomValues(new Uint32Array(4))).join('-');}catch{}return 'page-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);})();
 const state={phase:'checking',active:false,canControl:false,metrics:null,metricsStatus:'unavailable',checkedAt:null,confirmUntil:0,confirmToken:null,message:'',revision:-1,cached:false};
 let seq=0,countdownTimer=null,responseTimer=null,lastMetric=null;
 const labels={running:['Uruchomione','Gotowe do pracy.','Zatrzymaj Ubuntu'],offline:['Wyłączone','Uruchom, kiedy potrzebujesz.','Uruchom Ubuntu'],checking:['Sprawdzanie…','Ponowny odczyt stanu WSL.','Sprawdzanie…'],error:['Brak odczytu','Nie można ustalić stanu WSL.','Ponów odczyt'],starting:['Uruchamianie…','Przygotowywanie środowiska.','Uruchamianie…'],stopping:['Zatrzymywanie…','Kończenie sesji Ubuntu.','Zatrzymywanie…'],confirm:['Uruchomione','Gotowe do pracy.','Tak, zatrzymaj']};
 const details={cpu:['Procesor','Próbka obciążenia z /proc/stat, uśredniona dla wszystkich procesorów logicznych jądra WSL. Przy kilku dystrybucjach CPU może być współdzielone.'],ram:['Pamięć','MemTotal minus MemAvailable z /proc/meminfo. To pamięć jądra WSL, a nie cały RAM Windows; przy wielu dystrybucjach może być wspólna.'],disk:['Dysk Ubuntu','Zajęte miejsce i pojemność systemu plików / tej dystrybucji, w GB (10⁹ bajtów). To nie fizyczny rozmiar pliku VHDX.'],uptime:['Czas pracy','Czas od uruchomienia procesu init (PID 1) tej dystrybucji. Wyliczany z /proc/1/stat i zegara /proc/uptime.']};
 function icon(id,name){$(id).setAttribute('href','#i-'+name);}
 function bindBridge(){
   const candidate=window.chrome?.webview;
   if(!candidate||typeof candidate.postMessage!=='function'||typeof candidate.addEventListener!=='function'){
     bridgeHealth='missing';return false;
   }
   try{
     if(candidate!==subscribed){candidate.addEventListener('message',receive);subscribed=candidate;}
     bridge=candidate;return true;
   }catch{bridgeHealth='listen-error';return false;}
 }
 function send(kind,extra={}){
   if(!session||!bindBridge())return false;
   // WebView2.postMessage accepts one JSON-serializable argument. It is not
   // Window.postMessage(message, targetOrigin). Never send to a parent frame.
   // Keep the registered source-origin check without inventing an overload.
   try{
     if(new URL(sourceUrl).origin!==targetOrigin){bridgeHealth='origin-mismatch';return false;}
     bridge.postMessage(JSON.stringify({protocol:1,kind,session,client,seq:++seq,...extra}));
     if(kind==='ready'&&!connected)bridgeHealth='posted';
     return true;
   }catch{bridgeHealth='post-error';connected=false;return false;}
 }
 function cacheView(){
   if(!session||!state.checkedAt||!['running','offline'].includes(state.phase))return;
   try{localStorage.setItem(cacheKey,JSON.stringify({schema:1,phase:state.phase,checkedAt:state.checkedAt,metrics:state.phase==='running'?state.metrics:null,metricsStatus:state.metricsStatus}));}catch{}
 }
 function restoreView(){
   try{
     const text=localStorage.getItem(cacheKey)||'null';if(text.length>8192)return;
     const saved=JSON.parse(text),age=Date.now()-Date.parse(saved?.checkedAt);
     if(saved?.schema!==1||!['running','offline'].includes(saved.phase)||!Number.isFinite(age)||age< -60000||age>86400000)return;
     Object.assign(state,{phase:saved.phase,checkedAt:saved.checkedAt,metrics:saved.metrics||null,metricsStatus:saved.metrics?'stale':saved.metricsStatus,cached:true});
   }catch{}
 }
 function stopConnectionWork(){
   clearTimeout(retryTimer);retryTimer=null;clearTimeout(fallbackTimer);fallbackTimer=null;
   if(requestAbort){requestAbort.abort();requestAbort=null;}
 }
 function scheduleFallback(delay=20000){
   clearTimeout(fallbackTimer);
   if(document.hidden||closedByHost)return;
   fallbackTimer=setTimeout(async()=>{
     fallbackTimer=null;if(document.hidden||closedByHost)return;
     if(Date.now()-lastNativeAt>25000){connected=false;await readState();send('ready');}
     scheduleFallback();
   },delay);
 }
 async function readState(){
   if(document.hidden||closedByHost||!session||requestAbort)return;
   const abort=new AbortController();requestAbort=abort;
   const deadline=setTimeout(()=>abort.abort(),2500);
   try{
     // Only the allowlisted local provider resource. This GET cannot execute commands.
     const response=await fetch(sourceUrl,{method:'GET',headers:{'X-DevStack-View':'state','X-DevStack-Bridge':bridgeHealth,'Accept':'application/json'},credentials:'omit',cache:'no-store',redirect:'error',signal:abort.signal});
     if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('state response');
     const text=await response.text();if(text.length>32768)throw new Error('state size');
     if(!document.hidden&&!closedByHost)applyState(JSON.parse(text),false);
   }catch{
     if(!document.hidden&&!closedByHost&&!connected){state.message='Nie udało się odczytać stanu. Spróbuj synchronizacji.';if(!state.checkedAt)state.phase='error';render();}
   }finally{clearTimeout(deadline);if(requestAbort===abort)requestAbort=null;}
 }
 function hello(){
   clearTimeout(retryTimer);retryTimer=null;
   if(document.hidden||closedByHost)return;
   // Retry handshake only. Start and Stop are NEVER retried automatically.
   send('ready');readyAttempts++;
   if(connected)return;
   if(readyAttempts<8)retryTimer=setTimeout(hello,[150,300,600,1000,1500,2000,2500][readyAttempts-1]||2500);
   else{
     if(!state.checkedAt)state.phase='error';
     state.message='Brak połączenia sterowania. Dane będą odczytywane bez uruchamiania WSL.';
     state.canControl=false;render();readState();scheduleFallback();
   }
 }
 function connect(){
   stopConnectionWork();if(document.hidden)return;
   closedByHost=false;connected=false;readyAttempts=0;state.canControl=false;
   if(state.checkedAt)state.cached=true;
   // Paint BEFORE calling the potentially not-yet-injected native bridge.
   render();hello();
   readState().then(()=>{if(!document.hidden&&!closedByHost){send('ready');scheduleFallback(1500);}});
 }
 function time(value){const date=new Date(value);return value&&Number.isFinite(date.getTime())?date.toLocaleTimeString('pl-PL',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';}
 function number(value,digits=1){return value.toLocaleString('pl-PL',{maximumFractionDigits:digits,minimumFractionDigits:digits});}
 function finite(value){return typeof value==='number'&&Number.isFinite(value)&&value>=0;}
 function pct(used,total){return total>0?Math.max(0,Math.min(100,used/total*100)):0;}
 function setMetric(prefix,value,unit,small,percent){
   $(prefix+'Value').textContent=value;$(prefix+'Unit').textContent=unit;$(prefix+'Small').textContent=small;
   const bar=$(prefix+'Bar');if(bar)bar.style.width=percent+'%';
 }
 function renderMetrics(){
   const live=['running','confirm'].includes(state.phase);
   const m=live&&['ok','stale'].includes(state.metricsStatus)?state.metrics:null;
   for(const prefix of ['cpu','ram','disk'])setMetric(prefix,'—','','—',0);
   $('uptimeValue').textContent='—';$('uptimeUnit').textContent='';$('uptimeSmall').textContent=state.phase==='offline'?'System zatrzymany':'Brak danych';
   $('metricCPU').dataset.level='normal';$('metricRAM').dataset.level='normal';
   document.querySelector('.data-demo').textContent=m?(state.cached||state.metricsStatus==='stale'?'OSTATNI ODCZYT':'ODCZYT LIVE'):state.phase==='offline'?'WSL OFF':'BRAK DANYCH';
   if(!m)return;
   if(finite(m.cpuPercent)){const n=Math.round(m.cpuPercent);setMetric('cpu',String(n),'%',n+'%',Math.min(100,n));$('metricCPU').dataset.level=n>70?'high':'normal';}
   if(finite(m.memoryUsedBytes)&&finite(m.memoryTotalBytes)&&m.memoryTotalBytes>0){
     const p=pct(m.memoryUsedBytes,m.memoryTotalBytes);setMetric('ram',number(m.memoryUsedBytes/1e9),'GB','/ '+number(m.memoryTotalBytes/1e9,0)+' GB',p);$('metricRAM').dataset.level=p>70?'high':'normal';}
   if(finite(m.diskUsedBytes)&&finite(m.diskTotalBytes)&&m.diskTotalBytes>0)setMetric('disk',number(m.diskUsedBytes/1e9),'GB','/ '+number(m.diskTotalBytes/1e9,0)+' GB',pct(m.diskUsedBytes,m.diskTotalBytes));
   if(finite(m.uptimeSeconds)){
     const minutes=Math.floor(m.uptimeSeconds/60),hours=Math.floor(minutes/60),days=Math.floor(hours/24);
     $('uptimeValue').textContent=days?days+' d '+hours%24:hours?hours+' h '+minutes%60:String(minutes);
     $('uptimeUnit').textContent=days?'h':'min';$('uptimeSmall').textContent='Od uruchomienia';
   }
 }
 function stopCountdown(){clearInterval(countdownTimer);countdownTimer=null;}
 function tick(){
   const seconds=Math.max(0,Math.ceil((state.confirmUntil-Date.now())/1000));$('countdown').textContent=seconds+' s';
   if(!seconds){stopCountdown();state.phase='running';state.confirmToken=null;send('action',{verb:'stop-cancel'});render();}
 }
 function render(){
   const [title,subtitle,action]=labels[state.phase]||labels.error;
   const busy=['starting','stopping','checking'].includes(state.phase),confirm=state.phase==='confirm';
   $('widget').classList.toggle('offline',state.phase==='offline');$('widget').classList.toggle('error',state.phase==='error');$('widget').classList.toggle('busy',busy);$('widget').classList.toggle('paused',!state.active||document.hidden);
   $('statusTitle').textContent=title;$('statusSub').textContent=subtitle;$('actionLabel').textContent=action;
   $('mainAction').disabled=busy||document.hidden||closedByHost||(state.phase!=='error'&&(!state.active||!state.canControl));
   $('mainAction').className='main-action'+(state.phase==='running'?' stop':confirm?' confirm-stop':'');$('cancelButton').hidden=!confirm;
   icon('heroUse',busy?'refresh':state.phase==='error'?'warning':state.phase==='offline'?'power':'terminal');icon('actionUse',busy||state.phase==='error'?'refresh':'power');
   $('modeLine').classList.toggle('confirm',confirm);$('countdown').hidden=!confirm;
   icon('modeUse',confirm||state.phase==='error'||!!state.message?'warning':'shield');
   $('modeCopy').textContent=state.cached&&!state.message?'Poprzedni odczyt. Weryfikowanie stanu.':confirm?'Zatrzymasz wszystkie procesy Ubuntu.':state.message||(!state.canControl&&state.active?(connected?'Sterowanie wymaga zalogowania w Windows.':'Łączenie ze sterowaniem Ubuntu.'):state.phase==='offline'?'WSL nie uruchamia się automatycznie.':state.phase==='error'?'Brak danych nie oznacza wyłączenia.':busy?'Oczekiwanie na wynik polecenia.':'Odczyt tylko przy otwartym panelu.');
   $('syncTime').textContent=time(state.checkedAt);$('syncButton').disabled=['starting','stopping'].includes(state.phase)||document.hidden||closedByHost;
   $('syncButton').title=state.cached?'Poprzedni pomiar - aktualizowanie':'Odśwież dane';
   renderMetrics();
   stopCountdown();if(confirm&&state.active&&!document.hidden){tick();if(state.phase==='confirm')countdownTimer=setInterval(tick,250);}
 }
 function waitForReply(){
   clearTimeout(responseTimer);responseTimer=setTimeout(()=>{
     state.message='Brak odpowiedzi dostawcy. Otwórz panel ponownie.';render();
   },15000);
 }
 function action(verb){
   if(document.hidden||closedByHost)return;
   if(verb==='refresh'&&!connected){state.message='';connect();return;}
   if(!state.active)return;
   if(verb!=='refresh'&&!state.canControl)return;
   const token=verb==='stop-confirm'?state.confirmToken:undefined;
   if(verb==='stop-confirm'&&(!token||Date.now()>=state.confirmUntil))return;
   if(!send('action',{verb,confirmation:token})){state.message='Połączenie zostało przerwane. Polecenie nie jest ponawiane.';state.canControl=false;render();connect();return;}
   if(verb==='start')state.phase='starting';
   if(verb==='stop-confirm')state.phase='stopping';
   if(verb==='stop-cancel'){state.phase='running';state.confirmToken=null;}
   if(verb==='refresh'&&state.phase==='error')state.phase='checking';
   render();waitForReply();
 }
 function parseMessage(raw){
   let msg=raw;
   for(let i=0;i<2&&typeof msg==='string';i++){if(msg.length>32768)return null;try{msg=JSON.parse(msg);}catch{return null;}}
   return msg;
 }
 function applyState(msg,native){
   if(!msg||msg.protocol!==1||msg.kind!=='state'||msg.session!==session||!Number.isInteger(msg.revision)||!Object.prototype.hasOwnProperty.call(labels,msg.phase))return;
   if(native&&msg.client&&msg.client!==client)return;
   const server=typeof msg.server==='string'?msg.server:'legacy';
   if(currentServer!==server){
     if(retiredServers.has(server))return;
     if(currentServer)retiredServers.add(currentServer);
     currentServer=server;state.revision=-1;state.confirmToken=null;
   }
   if(msg.revision<=state.revision)return;
   // A delayed GET cannot overwrite a recent native confirmation or grant control.
   if(!native&&connected&&Date.now()-lastNativeAt<25000)return;
   clearTimeout(responseTimer);responseTimer=null;
   if(native){connected=true;bridgeHealth='connected';lastNativeAt=Date.now();clearTimeout(retryTimer);retryTimer=null;}
   Object.assign(state,{phase:msg.phase,active:msg.active===true,canControl:native&&msg.canControl===true,
     metrics:msg.metrics||null,metricsStatus:msg.metricsStatus||'unavailable',checkedAt:msg.checkedAt||null,
     confirmUntil:native?Number(msg.confirmUntil)||0:0,confirmToken:native&&typeof msg.confirmation==='string'?msg.confirmation:null,
     message:typeof msg.message==='string'?msg.message:'',revision:msg.revision,cached:msg.cached===true});
   if(!native&&state.phase==='confirm')state.phase='running';
   if(native&&!state.active){closedByHost=true;stopConnectionWork();stopCountdown();closeDetails();$('widgetMenu').hidden=true;}
   else if(native&&state.active){closedByHost=false;scheduleFallback();}
   cacheView();render();
   if(native)send('seen');
 }
 function receive(event){bridgeHealth='received';applyState(parseMessage(event?.data),true);}
 $('mainAction').addEventListener('click',()=>action(({offline:'start',running:'stop-ask',confirm:'stop-confirm',error:'refresh'})[state.phase]||''));
 $('cancelButton').addEventListener('click',()=>action('stop-cancel'));
 $('syncButton').addEventListener('click',()=>action('refresh'));
 $('menuRefresh').addEventListener('click',()=>{action('refresh');$('widgetMenu').hidden=true;});
 $('menuButton').addEventListener('click',e=>{e.stopPropagation();$('widgetMenu').hidden=!$('widgetMenu').hidden;$('menuButton').setAttribute('aria-expanded',String(!$('widgetMenu').hidden));});
 function closeDetails(){if(!$('metricPopover').hidden){$('metricPopover').hidden=true;if(lastMetric)lastMetric.focus();}}
 $('menuReset').addEventListener('click',()=>{closeDetails();$('widgetMenu').hidden=true;});
 document.querySelectorAll('[data-metric]').forEach(button=>button.addEventListener('click',()=>{
   const [title,body]=details[button.dataset.metric];lastMetric=button;$('detailTitle').textContent=title;
   const missing=state.metricsStatus==='not-installed'?' Moduł pomiarów Ubuntu nie jest skonfigurowany. Uruchom dołączony Install-WslTelemetry.ps1.':state.metricsStatus==='error'?' Ostatni odczyt zasobów nie powiódł się.':' ';
   $('detailBody').textContent=body+missing;$('metricPopover').hidden=false;$('closeDetail').focus();
 }));
 $('closeDetail').addEventListener('click',closeDetails);
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(!$('metricPopover').hidden)closeDetails();else if(state.phase==='confirm')action('stop-cancel');$('widgetMenu').hidden=true;}});
 document.addEventListener('click',event=>{if(!$('widgetMenu').contains(event.target)&&!$('menuButton').contains(event.target))$('widgetMenu').hidden=true;});
 document.addEventListener('visibilitychange',()=>{
   if(document.hidden){cacheView();stopConnectionWork();stopCountdown();clearTimeout(responseTimer);closeDetails();send('hidden');state.active=false;state.canControl=false;state.confirmToken=null;connected=false;if(state.phase==='confirm')state.phase='running';render();}
   else connect();
 });
 const media=matchMedia('(prefers-color-scheme: light)');
 function theme(first=true){const forced=first?query.get('theme'):null;document.documentElement.dataset.theme=forced==='light'||forced==='dark'?forced:media.matches?'light':'dark';}
 media.addEventListener('change',()=>theme(false));theme();
 restoreView();
 if(boot?.protocol===1&&boot.session===session){
   const newer=Date.parse(boot.checkedAt)>Date.parse(state.checkedAt||0);
   if(newer||!state.checkedAt){state.phase=Object.prototype.hasOwnProperty.call(labels,boot.phase)?boot.phase:'checking';state.checkedAt=boot.checkedAt||null;state.metrics=boot.metrics||null;state.metricsStatus=boot.metrics?'stale':boot.metricsStatus||'unavailable';state.cached=!!boot.checkedAt;}
 }
 window.addEventListener('error',()=>{state.message='Błąd interfejsu WSL. Sprawdź diagnostykę połączenia.';state.canControl=false;if(!state.checkedAt)state.phase='error';render();send('diagnostic');});
 window.addEventListener('pageshow',event=>{if(event.persisted)connect();});
 window.addEventListener('pagehide',()=>{cacheView();stopConnectionWork();stopCountdown();clearTimeout(responseTimer);send('hidden');});
 render();
 if(session)connect();else{state.phase='error';state.message='Ten widok działa wewnątrz panelu Windows.';render();}
})();
