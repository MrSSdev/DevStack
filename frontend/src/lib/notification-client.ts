// Each live document lifetime has its own lease. Never reuse an ID after
// pagehide or after a successful new login in the same document.
export let notificationClient=crypto.randomUUID().replaceAll('-','')
export function renewNotificationClient(){
  notificationClient=crypto.randomUUID().replaceAll('-','')
  return notificationClient
}
export const notificationChange='devstack:notifications-changed'
export const notificationDismissed='devstack:notification-dismissed'
export const notificationCategories=[
  {id:'mail',label:'Nowa poczta'},
  {id:'project',label:'Projekty i konfiguracja'},
  {id:'installer',label:'Instalatory'},
  {id:'service',label:'Usługi'},
] as const

export type Notice={
  id:string;category:string;title:string;description:string;route:string
  kind:'loading'|'success'|'error'|'info';created:number;updated:number;unread:boolean
}
export type NoticeState={items:Notice[];toast:Notice[];push_categories:string[];subscribed:boolean}
export type NoticeRequest=<T>(method:'GET'|'POST',path:string,csrf?:string,data?:unknown)=>Promise<T>
export const emptyNoticeState:NoticeState={items:[],toast:[],push_categories:['mail','project','installer','service'],subscribed:false}

export function isNoticeRoute(route:string):boolean{
  return /^\/#\/(?:dashboard|settings|projects|mail(?:\/[A-Za-z0-9]{22})?|project\/[a-z][a-z0-9-]{1,39})$/.test(route)
}
// Notification clicks use an opaque ID in the hash, never a secret or an
// arbitrary URL. Accept only a known DevStack route and a complete event ID.
export function notificationClickId(hash=window.location.hash):string|undefined{
  const index=hash.indexOf('?notice=')
  if(index<0||!isNoticeRoute('/'+hash.slice(0,index)))return undefined
  const id=hash.slice(index+'?notice='.length)
  return /^(?:(?:op|job|batch|service)-[a-f0-9]{32}|mail-[A-Za-z0-9]{22})$/.test(id)?id:undefined
}
export function notificationRouteHash(hash=window.location.hash):string{
  return notificationClickId(hash)?hash.slice(0,hash.indexOf('?notice=')):hash
}
// Edge maps registered web+devstack URIs to a percent-encoded manifest
// fragment. Normalize before React reads the route or acknowledges a notice.
// Only fixed local destinations are accepted; never navigate to a supplied URL.
const protocolPrefix='#/protocol?uri='
const protocolScheme='web+devstack://open/'
export function protocolLaunchHash(hash:string):string|undefined{
  if(!hash.startsWith(protocolPrefix))return undefined
  const fallback='#/dashboard'
  if(hash.length>512)return fallback
  let uri:string
  try{uri=decodeURIComponent(hash.slice(protocolPrefix.length))}catch{return fallback}
  if(!uri.startsWith(protocolScheme)||/[\x00-\x20\x7f]/.test(uri))return fallback
  const target='#/'+uri.slice(protocolScheme.length)
  const route=notificationRouteHash(target)
  if(!/^#\/(?:dashboard|projects|settings|mail(?:\/[A-Za-z0-9]{22})?)$/.test(route))return fallback
  const id=notificationClickId(target)
  if(route.startsWith('#/mail/')&&id&&id!=='mail-'+route.slice('#/mail/'.length))return fallback
  return target
}
export function normalizeProtocolLaunch(){
  const hash=protocolLaunchHash(window.location.hash)
  if(hash!==undefined)window.history.replaceState(window.history.state,'',window.location.pathname+window.location.search+hash)
}
export function consumeNotificationClick(id:string){
  // Another click/navigation may have occurred while the request was in
  // flight. Never overwrite a newer route. Keep the marker until success.
  if(notificationClickId()===id){
    window.history.replaceState(window.history.state,'',window.location.pathname+window.location.search+notificationRouteHash())
  }
}
// Only terminal toast closures in the currently visible document may
// acknowledge an event. A hidden tab or retired client must not silently
// read a notice the user may never have seen.
export function readNoticeOnClose(id:unknown,client=notificationClient){
  if(typeof id!=='string'||!/^(?:(?:op|job|batch|service)-[a-f0-9]{32}|mail-[A-Za-z0-9]{22})$/.test(id))return undefined
  let closed=false
  return ()=>{
    if(closed)return
    closed=true
    if(client===notificationClient&&document.visibilityState==='visible'){
      window.dispatchEvent(new CustomEvent(notificationDismissed,{detail:{id,client}}))
    }
  }
}
export function acknowledgeOperation(id:unknown,csrf:string,client=notificationClient){
  if(typeof id!=='string'||!/^op-[a-f0-9]{32}$/.test(id))return
  // A failure to acknowledge history must not make the actual operation fail.
  void fetch('/api/notifications/ack',{method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
    body:JSON.stringify({client,id})})
    .catch(()=>undefined).finally(()=>window.dispatchEvent(new Event(notificationChange)))
}

// Remember only IDs explicitly retired by pagehide, never a live document
// ID or a CSRF token. Duplicated tabs can copy sessionStorage safely: these
// IDs already belong to stopped lifetimes. Storage is best effort.
const retiredKey='devstack:retired-notification-clients'
// An older controller can remain active briefly during an update. Use the
// script URL to distinguish workers that can handle the leave message.
const pwaWorker='/sw.js?presence=1'
export function retiredNotificationClients():string[]{
  try{
    const value:unknown=JSON.parse(sessionStorage.getItem(retiredKey)??'[]')
    return Array.isArray(value)?[...new Set(value.filter((id):id is string=>typeof id==='string'&&/^[a-f0-9]{32}$/.test(id)))].slice(-16):[]
  }catch{return []}
}
function saveRetiredNotificationClients(ids:string[]){
  try{
    if(ids.length)sessionStorage.setItem(retiredKey,JSON.stringify([...new Set(ids)].slice(-16)))
    else sessionStorage.removeItem(retiredKey)
  }catch{/* The leave transport and lease fallback remain available. */}
}
export function acknowledgeRetiredNotificationClients(ids:string[]){
  if(ids.length)saveRetiredNotificationClients(retiredNotificationClients().filter(id=>!ids.includes(id)))
}
export async function leaveNotifications(csrf:string,client=notificationClient){
  if(!csrf)return
  saveRetiredNotificationClients([...retiredNotificationClients(),client])
  const controller=navigator.serviceWorker?.controller
  if(controller?.scriptURL===new URL(pwaWorker,window.location.origin).href){
    try{
      controller.postMessage({type:'devstack:notification-leave',client,csrf})
      return
    }catch{/* Fall through to the document transport. */}
  }
  // An uncontrolled page or one with an older worker can still send leave.
  try{
    await fetch('/api/notifications/leave',{method:'POST',credentials:'same-origin',keepalive:true,
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({client})})
  }catch{
    // Try beacon if fetch fails while this document can still run code.
    // The next authenticated poll or lease expiry covers a lost request.
    try{
      navigator.sendBeacon?.('/api/notifications/leave',
        new Blob([JSON.stringify({client,csrf})],{type:'application/json'}))
    }catch{/* Best effort during document teardown. */}
  }
}

export async function updateAppBadge(count:number){
  const appNavigator=navigator as Navigator&{setAppBadge?:(n:number)=>Promise<void>;clearAppBadge?:()=>Promise<void>}
  try{
    if(count>0)await appNavigator.setAppBadge?.(count)
    else await appNavigator.clearAppBadge?.()
  }catch{/* Optional platform feature. */}
}

export function registerPwa(){
  if('serviceWorker' in navigator&&window.isSecureContext){
    void navigator.serviceWorker.register(pwaWorker,{scope:'/',updateViaCache:'none'}).catch(()=>undefined)
  }
}
function decodeKey(key:string):Uint8Array<ArrayBuffer>{
  const raw=atob(key.replaceAll('-','+').replaceAll('_','/')+'='.repeat((4-key.length%4)%4))
  return Uint8Array.from(raw,c=>c.charCodeAt(0))
}
async function readyWorker(){
  if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){
    throw new Error('Ta przeglądarka nie obsługuje powiadomień Web Push.')
  }
  await navigator.serviceWorker.register(pwaWorker,{scope:'/',updateViaCache:'none'})
  return await new Promise<ServiceWorkerRegistration>((resolve,reject)=>{
    const timer=window.setTimeout(()=>reject(new Error('Service worker nie jest jeszcze aktywny. Odśwież aplikację.')),10000)
    void navigator.serviceWorker.ready.then(reg=>{window.clearTimeout(timer);resolve(reg)},error=>{window.clearTimeout(timer);reject(error)})
  })
}

export async function enablePush(request:NoticeRequest,csrf:string){
  if(!('Notification' in window)||!('PushManager' in window))throw new Error('Ta przeglądarka nie obsługuje Web Push.')
  // Browser permission is requested only on an explicit click, never on load.
  if(await Notification.requestPermission()!=='granted')throw new Error('Przeglądarka nie zezwoliła na powiadomienia.')
  const {key}=await request<{key:string}>('POST','/api/notifications/push-key',csrf,{})
  const bytes=decodeKey(key)
  if(bytes.length!==65||bytes[0]!==4)throw new Error('Nieprawidłowy publiczny klucz Web Push.')
  const reg=await readyWorker()
  let subscription=await reg.pushManager.getSubscription()
  if(subscription){
    const old=subscription.options.applicationServerKey
    if(!old||new Uint8Array(old).join(',')!==bytes.join(',')){await subscription.unsubscribe();subscription=null}
  }
  const existing=!!subscription
  if(!subscription)subscription=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes})
  try{
    await request('POST','/api/notifications/subscribe',csrf,{subscription:subscription.toJSON()})
  }catch(e){
    if(!existing)await subscription.unsubscribe().catch(()=>false)
    throw e
  }
}
export async function disablePush(request:NoticeRequest,csrf:string){
  // Remove the server subscription even when this is a different profile.
  await request('POST','/api/notifications/unsubscribe',csrf,{})
  if('serviceWorker' in navigator){
    const reg=await navigator.serviceWorker.getRegistration('/')
    if(reg){
      const subscription=await reg.pushManager.getSubscription()
      if(subscription)await subscription.unsubscribe().catch(()=>false)
    }
  }
}
