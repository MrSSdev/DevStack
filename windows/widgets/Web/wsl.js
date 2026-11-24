'use strict';
// Production renderer. No demo readings, polling timers, fetch or shell commands.
(() => {
 const $ = id => document.getElementById(id);
 const query = new URLSearchParams(location.search);
 const session = query.get('session') || '';
 const bridge = window.chrome && window.chrome.webview;
 const client = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-');
 const state = {phase:'checking',active:false,canControl:false,metrics:null,metricsStatus:'unavailable',checkedAt:null,confirmUntil:0,confirmToken:null,message:'',revision:-1};
 let seq=0, countdownTimer=null, responseTimer=null, lastMetric=null;
 const labels={running:['Uruchomione','Gotowe do pracy.','Zatrzymaj Ubuntu'],offline:['Wy\u0142\u0105czone','Uruchom, kiedy potrzebujesz.','Uruchom Ubuntu'],checking:['Sprawdzanie\u2026','Ponowny odczyt stanu WSL.','Sprawdzanie\u2026'],error:['Brak odczytu','Nie mo\u017cna ustali\u0107 stanu WSL.','Pon\u00f3w odczyt'],starting:['Uruchamianie\u2026','Przygotowywanie \u015brodowiska.','Uruchamianie\u2026'],stopping:['Zatrzymywanie\u2026','Ko\u0144czenie sesji Ubuntu.','Zatrzymywanie\u2026'],confirm:['Uruchomione','Gotowe do pracy.','Tak, zatrzymaj']};
 const details={cpu:['Procesor','Pr\u00f3bka obci\u0105\u017cenia z /proc/stat, u\u015bredniona dla wszystkich procesor\u00f3w logicznych j\u0105dra WSL. Przy kilku dystrybucjach CPU mo\u017ce by\u0107 wsp\u00f3\u0142dzielone.'],ram:['Pami\u0119\u0107','MemTotal minus MemAvailable z /proc/meminfo. To pami\u0119\u0107 j\u0105dra WSL, a nie ca\u0142y RAM Windows; przy wielu dystrybucjach mo\u017ce by\u0107 wsp\u00f3lna.'],disk:['Dysk Ubuntu','Zaj\u0119te miejsce i pojemno\u015b\u0107 systemu plik\u00f3w / tej dystrybucji, w GB (10\u2079 bajt\u00f3w). To nie fizyczny rozmiar pliku VHDX.'],uptime:['Czas pracy','Czas od uruchomienia procesu init (PID 1) tej dystrybucji. Wyliczany z /proc/1/stat i zegara /proc/uptime.']};
 function icon(id,name){$(id).setAttribute('href','#i-'+name);}
 function send(kind, extra={}) {
   if (!bridge || !session) return false;
   bridge.postMessage(JSON.stringify({protocol:1,kind,session,client,seq:++seq,...extra}),location.origin);
   return true;
 }
 function time(value){const date=new Date(value);return value && Number.isFinite(date.getTime())?date.toLocaleTimeString('pl-PL',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'\u2014';}
 function number(value,digits=1){return value.toLocaleString('pl-PL',{maximumFractionDigits:digits,minimumFractionDigits:digits});}
 function finite(value){return typeof value==='number' && Number.isFinite(value) && value>=0;}
 function pct(used,total){return total>0?Math.max(0,Math.min(100,used/total*100)):0;}
 function setMetric(prefix,value,unit,small,percent){
   $(prefix+'Value').textContent=value;$(prefix+'Unit').textContent=unit;
   $(prefix+'Small').textContent=small;const bar=$(prefix+'Bar');if(bar)bar.style.width=percent+'%';
 }
 function renderMetrics(){
   const live=['running','confirm'].includes(state.phase);
   const m=live&&state.metricsStatus==='ok'?state.metrics:null;
   for(const prefix of ['cpu','ram','disk'])setMetric(prefix,'\u2014','','\u2014',0);
   $('uptimeValue').textContent='\u2014';$('uptimeUnit').textContent='';$('uptimeSmall').textContent=state.phase==='offline'?'System zatrzymany':'Brak danych';
   $('metricCPU').dataset.level='normal';$('metricRAM').dataset.level='normal';
   document.querySelector('.data-demo').textContent=m?'ODCZYT LIVE':state.phase==='offline'?'WSL OFF':'BRAK DANYCH';
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
   $('mainAction').disabled=busy||!state.active||(!state.canControl&&state.phase!=='error');
   $('mainAction').className='main-action'+(state.phase==='running'?' stop':confirm?' confirm-stop':'');$('cancelButton').hidden=!confirm;
   icon('heroUse',busy?'refresh':state.phase==='error'?'warning':state.phase==='offline'?'power':'terminal');icon('actionUse',busy||state.phase==='error'?'refresh':'power');
   $('modeLine').classList.toggle('confirm',confirm);$('countdown').hidden=!confirm;
   icon('modeUse',confirm||state.phase==='error'||!!state.message?'warning':'shield');
   $('modeCopy').textContent=confirm?'Zatrzymasz wszystkie procesy Ubuntu.':state.message||(!state.canControl&&state.active?'Sterowanie wymaga zalogowania w Windows.':state.phase==='offline'?'WSL nie uruchamia si\u0119 automatycznie.':state.phase==='error'?'Brak danych nie oznacza wy\u0142\u0105czenia.':busy?'Oczekiwanie na wynik polecenia.':'Odczyt tylko przy otwartym panelu.');
   $('syncTime').textContent=time(state.checkedAt);$('syncButton').disabled=busy||!state.active;
   renderMetrics();
   stopCountdown();if(confirm&&state.active&&!document.hidden){tick();if(state.phase==='confirm')countdownTimer=setInterval(tick,250);}
 }
 function waitForReply(){
   clearTimeout(responseTimer);responseTimer=setTimeout(()=>{
     // Do not claim the operation failed or completed: its result is unknown.
     state.message='Brak odpowiedzi dostawcy. Otw\u00f3rz panel ponownie.';render();
   },45000);
 }
 function action(verb){
   if(!state.active||document.hidden)return;
   if(verb!=='refresh'&&!state.canControl)return;
   const token=verb==='stop-confirm'?state.confirmToken:undefined;
   if(verb==='stop-confirm'&&(!token||Date.now()>=state.confirmUntil))return;
   if(!send('action',{verb,confirmation:token}))return;
   if(verb==='start')state.phase='starting';
   if(verb==='stop-confirm')state.phase='stopping';
   if(verb==='stop-cancel'){state.phase='running';state.confirmToken=null;}
   if(verb==='refresh'&&state.phase==='error')state.phase='checking';
   render();waitForReply();
 }
 function receive(event){
   let msg;try{msg=typeof event.data==='string'?JSON.parse(event.data):event.data;}catch{return;}
   if(!msg||msg.protocol!==1||msg.kind!=='state'||msg.session!==session||!Number.isInteger(msg.revision)||msg.revision<=state.revision||!Object.hasOwn(labels,msg.phase))return;
   clearTimeout(responseTimer);responseTimer=null;
   Object.assign(state,{phase:msg.phase,active:msg.active===true,canControl:msg.canControl===true,metrics:msg.metrics||null,metricsStatus:msg.metricsStatus||'unavailable',checkedAt:msg.checkedAt||null,confirmUntil:Number(msg.confirmUntil)||0,confirmToken:typeof msg.confirmation==='string'?msg.confirmation:null,message:typeof msg.message==='string'?msg.message:'',revision:msg.revision});
   if(!state.active){stopCountdown();closeDetails();$('widgetMenu').hidden=true;}
   render();
 }
 $('mainAction').addEventListener('click',()=>action(({offline:'start',running:'stop-ask',confirm:'stop-confirm',error:'refresh'})[state.phase]||''));
 $('cancelButton').addEventListener('click',()=>action('stop-cancel'));
 $('syncButton').addEventListener('click',()=>action('refresh'));
 $('menuRefresh').addEventListener('click',()=>{action('refresh');$('widgetMenu').hidden=true;});
 $('menuButton').addEventListener('click',e=>{e.stopPropagation();$('widgetMenu').hidden=!$('widgetMenu').hidden;$('menuButton').setAttribute('aria-expanded',String(!$('widgetMenu').hidden));});
 function closeDetails(){if(!$('metricPopover').hidden){$('metricPopover').hidden=true;if(lastMetric)lastMetric.focus();}}
 $('menuReset').addEventListener('click',()=>{closeDetails();$('widgetMenu').hidden=true;});
 document.querySelectorAll('[data-metric]').forEach(button=>button.addEventListener('click',()=>{
   const [title,body]=details[button.dataset.metric];lastMetric=button;$('detailTitle').textContent=title;
   const missing=state.metricsStatus==='not-installed'?' Modu\u0142 pomiar\u00f3w Ubuntu nie jest skonfigurowany. Uruchom do\u0142\u0105czony Install-WslTelemetry.ps1.':state.metricsStatus==='error'?' Ostatni odczyt zasob\u00f3w nie powi\u00f3d\u0142 si\u0119.':' ';
   $('detailBody').textContent=body+missing;$('metricPopover').hidden=false;$('closeDetail').focus();
 }));
 $('closeDetail').addEventListener('click',closeDetails);
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(!$('metricPopover').hidden)closeDetails();else if(state.phase==='confirm')action('stop-cancel');$('widgetMenu').hidden=true;}});
 document.addEventListener('click',event=>{if(!$('widgetMenu').contains(event.target)&&!$('menuButton').contains(event.target))$('widgetMenu').hidden=true;});
 document.addEventListener('visibilitychange',()=>{
   if(document.hidden){stopCountdown();clearTimeout(responseTimer);closeDetails();send('hidden');state.active=false;state.confirmToken=null;if(state.phase==='confirm')state.phase='running';render();}
   else send('ready');
 });
 const media=matchMedia('(prefers-color-scheme: light)');
 function theme(first=true){const forced=first?query.get('theme'):null;document.documentElement.dataset.theme=forced==='light'||forced==='dark'?forced:media.matches?'light':'dark';}
 media.addEventListener('change',()=>theme(false));theme();
 if(bridge&&session){bridge.addEventListener('message',receive);send('ready');waitForReply();}
 else{state.phase='error';state.message='Ten widok dzia\u0142a wewn\u0105trz panelu Windows.';}
 render();
})();
