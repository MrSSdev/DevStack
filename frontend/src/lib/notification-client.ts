// Each document has its own lease; duplicated tabs must not share an ID.
export const notificationClient=crypto.randomUUID().replaceAll('-','')
export const notificationChange='devstack:notifications-changed'
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
export function acknowledgeOperation(id:unknown,csrf:string){
  if(typeof id!=='string'||!/^op-[a-f0-9]{32}$/.test(id))return
  // A failure to acknowledge history must not make the actual operation fail.
  void fetch('/api/notifications/ack',{method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
    body:JSON.stringify({client:notificationClient,id})})
    .catch(()=>undefined).finally(()=>window.dispatchEvent(new Event(notificationChange)))
}

export function leaveNotifications(csrf:string){
  if(!csrf)return
  void fetch('/api/notifications/leave',{method:'POST',credentials:'same-origin',keepalive:true,
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
    body:JSON.stringify({client:notificationClient})}).catch(()=>undefined)
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
    void navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'}).catch(()=>undefined)
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
  await navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'})
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
