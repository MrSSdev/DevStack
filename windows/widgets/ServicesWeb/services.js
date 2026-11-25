'use strict';
// Read-only provider fallback only while visible; no demo readings or shell commands.
(() => {
 const $ = id => document.getElementById(id);
 const query = new URLSearchParams(location.search);
 let boot=null;try{boot=JSON.parse($('devstack-bootstrap')?.textContent||'null');}catch{}
 const session = query.get('session') || boot?.session || '';
 const targetOrigin=document.documentElement.dataset.origin||location.origin;
 const sourceUrl=boot?.documentUri||location.href;
 let bridge=null,subscribed=null,connected=false,closedByHost=false,readyAttempts=0,retryTimer=null,fallbackTimer=null,requestAbort=null;
 let bridgeHealth='not-attempted';
 let currentServer=null,lastNativeAt=0;const retiredServers=new Set();
 const cacheKey='devstack-services-view-1-'+session;
 // client is a correlation ID, never an authentication or confirmation token.
 const client=(()=>{try{const c=globalThis.crypto;if(typeof c?.randomUUID==='function')return c.randomUUID();if(c?.getRandomValues)return Array.from(c.getRandomValues(new Uint32Array(4))).join('-');}catch{}return 'page-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);})();
 const state={phase:'checking',active:false,canControl:false,canNavigate:false,services:[],projectCount:null,integration:'unavailable',checkedAt:null,confirmation:null,confirmService:null,confirmUntil:0,busy:null,restartStatus:null,message:'',revision:-1,cached:false};
 let seq=0,countdownTimer=null,responseTimer=null,noticeTimer=null,infoService=null,infoReturnFocus=null;
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
   try{localStorage.setItem(cacheKey,JSON.stringify({schema:1,phase:state.phase,checkedAt:state.checkedAt,services:state.phase==='running'?state.services:[],projectCount:state.projectCount,integration:state.integration}));}catch{}
 }
 function restoreView(){
   try{
     const text=localStorage.getItem(cacheKey)||'null';if(text.length>8192)return;
     const saved=JSON.parse(text),age=Date.now()-Date.parse(saved?.checkedAt);
     if(saved?.schema!==1||!['running','offline'].includes(saved.phase)||!Number.isFinite(age)||age< -60000||age>86400000)return;
     Object.assign(state,{phase:saved.phase,checkedAt:saved.checkedAt,services:Array.isArray(saved.services)?saved.services:[],projectCount:saved.projectCount,integration:saved.integration,cached:true});
   }catch{}
 }
 function stopConnectionWork(){
   clearTimeout(retryTimer);retryTimer=null;clearTimeout(fallbackTimer);fallbackTimer=null;clearTimeout(noticeTimer);noticeTimer=null;
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
     state.message='Brak połączenia sterowania. Odśwież widżet.';
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
 const defs=[
  {id:'nginx',name:'Nginx',description:'Serwer WWW i lokalne połączenia HTTPS.',probe:'HTTPS (całe żądanie)',endpoint:'devstack.test/api/session'},
  {id:'php',name:'PHP-FPM',description:'Procesy wykonujące aplikacje PHP.',probe:'HTTPS/PHP (całe żądanie)',endpoint:'devstack.test/api/session'},
  {id:'mariadb',name:'MariaDB',description:'Baza danych lokalnych projektów.',probe:'TCP (połączenie)',endpoint:'127.0.0.1:3306'},
  {id:'redis',name:'Redis',description:'Pamięć podręczna i kolejki aplikacji.',probe:'TCP (połączenie)',endpoint:'127.0.0.1:6379'}
 ];
 const labels={checking:1,running:1,offline:1,error:1};
 const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0;
 const number=(n,d=0)=>n.toLocaleString('pl-PL',{maximumFractionDigits:d});
 const time=v=>v&&!Number.isNaN(Date.parse(v))?new Date(v).toLocaleTimeString('pl-PL',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
 const data=id=>state.services.find(s=>s.id===id)||{};
 const status=id=>state.busy===id?'busy':state.phase==='offline'?'off':data(id).reachable===true?'up':data(id).reachable===false?'down':'unknown';
 const statusName=id=>({busy:'Restartowanie',off:'Nieaktywne',up:'Dostępne',down:'Niedostępne',unknown:'Brak danych'}[status(id)]);
 const canRestart=id=>connected&&state.canControl&&state.active&&!state.cached&&!closedByHost&&!document.hidden&&!state.busy&&typeof data(id).unitActive==='boolean';
 function validNotice(value){
  if(!value||!defs.some(s=>s.id===value.service)||!['restarting','verifying','success','error','unconfirmed','not-run'].includes(value.phase))return null;
  if(value.phase==='success'&&(!Number.isFinite(value.expiresAt)||value.expiresAt<=Date.now()))return null;
  return value;
 }
 function renderNotice(){
  clearTimeout(noticeTimer);noticeTimer=null;
  const notice=validNotice(state.restartStatus);state.restartStatus=notice;
  const names={restarting:'ponowne uruchamianie…',verifying:'restart zakończony. Weryfikowanie…',success:'zweryfikowano pomyślnie.',error:'błąd weryfikacji.',unconfirmed:'nie potwierdzono restartu.', 'not-run':'restart nie został wykonany.'};
  const line=$('widgetToast');
  const message=notice?defs.find(s=>s.id===notice.service).name+': '+names[notice.phase]:state.message;
  const kind=notice?.phase==='success'?'success':notice&&['error','unconfirmed','not-run'].includes(notice.phase)?'error':'info';
  line.hidden=!message;line.dataset.kind=kind;$('projectStatusBlock').classList.toggle('has-notice',!!message);
  $('widgetMessage').textContent=message||'';$('noticeIcon').setAttribute('href','#i-'+(kind==='success'?'check':kind==='error'?'warning':'info'));
  if(notice?.phase==='success'&&!document.hidden&&!closedByHost)noticeTimer=setTimeout(renderNotice,Math.max(0,notice.expiresAt-Date.now()));
 }
 function render(){
  const good=defs.filter(s=>data(s.id).reachable===true).length;
  let title='Gotowe do pracy',sub=`Dostępne usługi: ${good} z 4.`,glyph='grid';
  if(state.phase==='checking'){title='Sprawdzanie…';sub='Odczytywanie stanu usług.';}
  else if(state.phase==='offline'){title='Ubuntu wyłączone';sub='Uruchom je w widżecie WSL.';glyph='power';}
  else if(state.phase==='error'){title='Brak odczytu';sub='Sprawdź ponownie. Stan jest nieznany.';glyph='warning';}
  else if(state.busy){title='Restart usługi';sub='Pozostałe usługi bez zmian.';glyph='refresh';}
  else if(good<4){title='Sprawdź usługi';sub=`Dostępne usługi: ${good} z 4.`;glyph='warning';}
  $('statusTitle').textContent=title;$('statusSub').textContent=sub;$('heroUse').setAttribute('href','#i-'+glyph);
  $('widget').classList.toggle('offline',state.phase==='offline');$('widget').classList.toggle('error',state.phase==='error');
  $('widget').classList.toggle('partial',state.phase==='running'&&good<4);$('widget').classList.toggle('busy',!!state.busy);
  $('dataLabel').textContent=state.cached?'OSTATNI ODCZYT':state.phase==='offline'?'WSL OFF':state.phase==='running'?'ODCZYT LIVE':'BRAK DANYCH';
  defs.forEach(s=>{
   const m=data(s.id);$('service-'+s.id).dataset.status=status(s.id);$('service-'+s.id).setAttribute('aria-label',s.name+': '+statusName(s.id)+'. Otwórz szczegóły.');
   $('state-'+s.id).textContent=statusName(s.id);
   $('latency-'+s.id).textContent=finite(m.latencyMs)&&state.phase==='running'?number(m.latencyMs,1)+' ms':'— ms';
   $('ram-'+s.id).textContent=finite(m.memoryBytes)&&state.phase==='running'?number(m.memoryBytes/1e6,1)+' MB':'— MB';
  });
  $('projectSummary').textContent='Łącznie projektów: '+(Number.isInteger(state.projectCount)&&state.projectCount>=0?state.projectCount:'—');
  $('syncTime').textContent=time(state.checkedAt);$('syncButton').disabled=closedByHost||document.hidden||!!state.busy;
  $('footerMode').textContent=state.cached?'Poprzedni odczyt':'Na żądanie';
  ['addProjectButton','projectsButton'].forEach(id=>{
   const enabled=connected&&state.active&&state.canNavigate&&!state.cached&&!document.hidden;
   $(id).setAttribute('aria-disabled',String(!enabled));$(id).tabIndex=enabled?0:-1;
   if(enabled)$(id).href=$(id).dataset.url;else $(id).removeAttribute('href');
  });
  renderNotice();
  renderInfo();
 }
 function renderInfo(){
  if(!infoService||$('servicePopover').hidden)return;
  const def=defs.find(s=>s.id===infoService),m=data(infoService),allowed=canRestart(infoService);
  const confirming=allowed&&state.confirmService===infoService&&state.confirmation&&Date.now()<state.confirmUntil;
  $('serviceInfoTitle').textContent=def.name;$('serviceInfoDescription').textContent=def.description;
  $('serviceInfoBody').innerHTML=`<dl class="detail-table"><dt>Stan</dt><dd>${esc(statusName(infoService))}</dd><dt>Sprawdzane połączenie</dt><dd>${esc(def.probe)}</dd><dt>Cel pomiaru</dt><dd>${esc(def.endpoint)}</dd><dt>Czas odpowiedzi</dt><dd>${finite(m.latencyMs)?number(m.latencyMs,1)+' ms':'—'}</dd><dt>CPU usługi</dt><dd title="100% oznacza jeden procesor logiczny">${finite(m.cpuPercent)?number(m.cpuPercent,1)+'%':'—'}</dd><dt>Pamięć usługi</dt><dd title="MemoryCurrent grupy procesów systemd; obejmuje również pamięć podręczną">${finite(m.memoryBytes)?number(m.memoryBytes/1e6,1)+' MB':'—'}</dd></dl>`;
  const busy=state.busy===infoService,button=$('restartServiceButton');
  button.disabled=!allowed;button.classList.toggle('confirm-restart',!!confirming);button.dataset.busy=String(busy);
  $('restartServiceLabel').textContent=busy?'Restartowanie...':confirming?'Tak, restartuj':'Uruchom ponownie';
  $('cancelServiceRestart').hidden=!confirming;
  const prompt=$('restartPrompt');prompt.hidden=true;
  if(confirming){prompt.hidden=false;prompt.textContent='Restart '+def.name+' przerwie jej bieżące połączenia. Kontynuować?';}
  else if(!allowed&&!busy){prompt.hidden=false;prompt.textContent=state.phase==='offline'?'Najpierw uruchom Ubuntu w widżecie WSL.':state.integration==='not-installed'?'Uruchom Install-DevStackWidget.ps1, aby włączyć pomiary i restart.':state.integration!=='ok'?'Integracja usług nie odpowiada. Odśwież dane.':!connected?'Łączenie ze sterowaniem usług.':'Oczekiwanie na aktualny, uwierzytelniony stan.';}
 }
 function action(verb,service='',confirmation=null){
  if(document.hidden||closedByHost)return;
  if(verb==='refresh'&&!connected){connect();return;}
  if(!state.active)return;
  if(verb.startsWith('restart-')&&verb!=='restart-cancel'&&!canRestart(service))return;
  if(verb==='restart-confirm'&&(!confirmation||Date.now()>=state.confirmUntil))return;
  if(!send('action',{verb,service,confirmation})){state.canControl=false;state.message='Polecenie nie zostało ponowione. Otwórz panel ponownie.';render();return;}
  // Browser navigation is fire-and-forget; it has no service acknowledgement.
  if(verb==='open-new'||verb==='open-projects')return;
  if(verb==='restart-confirm'){state.busy=service;state.confirmation=null;state.confirmService=null;state.message='';state.restartStatus={service,phase:'restarting'};closeInfo();}
  if(verb==='restart-cancel'){state.confirmation=null;state.confirmService=null;}
  render();clearTimeout(responseTimer);responseTimer=setTimeout(()=>{
   if(verb==='restart-confirm'){state.busy=null;state.canControl=false;state.restartStatus={service,phase:'unconfirmed'};}
   else state.message='Brak potwierdzenia. Sprawdź stan przed ponowieniem.';
   render();
  },18000);
 }
 function closeInfo(focus=true){
  if(state.confirmation&&infoService===state.confirmService)action('restart-cancel');
  $('servicePopover').hidden=true;infoService=null;
  document.querySelectorAll('[data-service]').forEach(b=>b.setAttribute('aria-expanded','false'));
  if(focus&&infoReturnFocus)infoReturnFocus.focus();
 }
 function openInfo(id){
  if(infoService===id&&!$('servicePopover').hidden){closeInfo();return;}
  closeInfo(false);infoService=id;infoReturnFocus=$('service-'+id);$('servicePopover').hidden=false;
  $('service-'+id).setAttribute('aria-expanded','true');renderInfo();$('closeServiceInfo').focus();
 }
 function parseMessage(raw){let msg=raw;for(let i=0;i<2&&typeof msg==='string';i++){if(msg.length>32768)return null;try{msg=JSON.parse(msg);}catch{return null;}}return msg;}
 function applyState(msg,native){
  if(!msg||msg.protocol!==1||msg.kind!=='state'||msg.session!==session||!Number.isInteger(msg.revision)||!labels[msg.phase])return;
  if(native&&msg.client!==client)return;
  const server=typeof msg.server==='string'?msg.server:'legacy';
  if(server!==currentServer){if(retiredServers.has(server))return;if(currentServer)retiredServers.add(currentServer);currentServer=server;state.revision=-1;}
  if(msg.revision<=state.revision||!native&&connected&&Date.now()-lastNativeAt<25000)return;
  clearTimeout(responseTimer);
  if(native){connected=true;lastNativeAt=Date.now();bridgeHealth='connected';clearTimeout(retryTimer);}
  Object.assign(state,{phase:msg.phase,active:msg.active===true,canControl:native&&msg.canControl===true,canNavigate:native&&msg.canNavigate===true,
   services:Array.isArray(msg.services)?msg.services.filter(s=>s&&typeof s==='object'&&defs.some(d=>d.id===s.id)):[],projectCount:msg.projectCount,
   integration:msg.integration||'unavailable',cached:msg.cached===true,checkedAt:msg.checkedAt||null,busy:defs.some(s=>s.id===msg.busy)?msg.busy:null,
   confirmation:native&&typeof msg.confirmation==='string'?msg.confirmation:null,confirmService:native?msg.confirmService:null,confirmUntil:native?msg.confirmUntil:0,
   restartStatus:validNotice(msg.restartStatus),message:typeof msg.message==='string'?msg.message:'',revision:msg.revision});
  if(native&&!state.active){closedByHost=true;stopConnectionWork();state.confirmation=null;closeInfo(false);}
  else if(native){closedByHost=false;scheduleFallback();}
  clearTimeout(countdownTimer);
  if(state.confirmation)countdownTimer=setTimeout(()=>{state.confirmation=null;state.confirmService=null;renderInfo();},Math.max(0,state.confirmUntil-Date.now()));
  cacheView();render();if(native)send('seen');
 }
 function receive(event){bridgeHealth='received';applyState(parseMessage(event?.data),true);}
 document.querySelectorAll('[data-service]').forEach(b=>b.addEventListener('click',()=>openInfo(b.dataset.service)));
 $('closeServiceInfo').addEventListener('click',()=>closeInfo());
 $('restartServiceButton').addEventListener('click',()=>{
  if(!infoService)return;
  action(state.confirmService===infoService&&state.confirmation?'restart-confirm':'restart-ask',infoService,state.confirmation);
 });
 $('cancelServiceRestart').addEventListener('click',()=>action('restart-cancel'));
 $('syncButton').addEventListener('click',()=>action('refresh'));
 ['addProjectButton','projectsButton'].forEach((id,i)=>$(id).addEventListener('click',event=>{
  event.preventDefault();if($(id).getAttribute('aria-disabled')==='true')return;closeInfo(false);action(i===0?'open-new':'open-projects');
 }));
 document.addEventListener('click',e=>{if(!$('servicePopover').hidden&&!$('servicePopover').contains(e.target)&&!e.target.closest('[data-service]'))closeInfo(false);});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')closeInfo();});
 function suspend(){cacheView();stopConnectionWork();clearTimeout(responseTimer);clearTimeout(countdownTimer);state.confirmation=null;closeInfo(false);send('hidden');state.active=false;state.canControl=false;state.canNavigate=false;connected=false;render();}
 document.addEventListener('visibilitychange',()=>document.hidden?suspend():connect());
 const media=matchMedia('(prefers-color-scheme: light)');
 function theme(first=true){const forced=first?query.get('theme'):null;document.documentElement.dataset.theme=forced==='light'||forced==='dark'?forced:media.matches?'light':'dark';}
 media.addEventListener('change',()=>theme(false));theme();restoreView();
 if(boot?.protocol===1&&boot.session===session){
  state.restartStatus=validNotice(boot.restartStatus);
  if(!state.checkedAt||Date.parse(boot.checkedAt)>Date.parse(state.checkedAt))Object.assign(state,{phase:labels[boot.phase]?boot.phase:'checking',services:boot.services||[],projectCount:boot.projectCount,checkedAt:boot.checkedAt||null,integration:boot.integration||'unavailable',cached:!!boot.checkedAt});
 }
 window.addEventListener('error',()=>{state.message='Błąd interfejsu DevStack. Otwórz panel ponownie.';state.canControl=false;render();send('diagnostic');});
 window.addEventListener('pageshow',e=>{if(e.persisted)connect();});window.addEventListener('pagehide',suspend);
 render();if(session)connect();else{state.phase='error';state.message='Ten widok działa w panelu Windows.';render();}
})();
