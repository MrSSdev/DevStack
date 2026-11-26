/* DevStack PWA. Network-only: no cache of authenticated HTML, API responses,
 * passwords, mail, project data or attachments. This worker never starts WSL. */
'use strict';
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||event.request.mode!=='navigate'||url.origin!==self.location.origin||url.pathname!=='/')return;
  event.respondWith(fetch(event.request,{cache:'no-store'}).catch(()=>new Response(
    '<!doctype html><html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DevStack — niedostępny</title><body><main><h1>DevStack jest niedostępny</h1><p>Sprawdź, czy Ubuntu i usługi DevStack są uruchomione.</p><p><a href="/">Spróbuj ponownie</a></p></main></body></html>',
    {status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}})));
});
const validId=id=>typeof id==='string'&&/^(?:(?:op|job|batch|service)-[a-f0-9]{32}|mail-[A-Za-z0-9]{22})$/.test(id);
const routeFor=(category,id)=>category==='mail'?(id.startsWith('mail-')?'/#/mail/'+id.slice(5):'/#/mail'):
  category==='project'?'/#/projects':category==='installer'?'/#/projects':category==='settings'?'/#/settings':'/#/dashboard';
self.addEventListener('push',event=>{
  // Edge requires user-visible push. The server suppresses sends while a
  // client lease is active; a race after transmission must remain visible.
  event.waitUntil((async()=>{
    let data={};
    try{data=event.data?.json()||{}}catch{}
    const id=validId(data.id)?data.id:'update';
    const category=['mail','project','installer','service','settings'].includes(data.category)?data.category:'';
    const title=category==='mail'?'Nowa poczta w DevStack':category==='project'?'Zdarzenie projektu':
      category==='installer'?'Zakończono operację instalatora':category==='service'?'Zdarzenie usługi':'DevStack';
    await self.registration.showNotification(title,{
      body:data.kind==='error'?'Operacja wymaga uwagi. Otwórz DevStack.':'Otwórz DevStack, aby zobaczyć szczegóły.',
      icon:'/pwa/icon-192.png',badge:'/pwa/icon-192.png',tag:'devstack-'+id,renotify:false,
      data:{route:routeFor(category,id),id}
    });
    try{await self.navigator.setAppBadge?.()}catch{}
  })());
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const route=event.notification.data?.route;
  const safe=typeof route==='string'&&/^\/#\/(?:dashboard|projects|settings|mail(?:\/[A-Za-z0-9]{22})?)$/.test(route)?route:'/#/dashboard';
  // Keep the exact journal ID through startup and password login. Older
  // notifications already carry this ID in their stable tag. The fragment
  // stays local to the page; a normal authenticated CSRF-protected poll
  // marks the event read. No background authentication bypass.
  const supplied=event.notification.data?.id;
  const oldTag=typeof event.notification.tag==='string'&&event.notification.tag.startsWith('devstack-')
    ?event.notification.tag.slice('devstack-'.length):'';
  const id=validId(supplied)?supplied:validId(oldTag)?oldTag:'';
  const target=new URL(safe+(id?'?notice='+id:''),self.location.origin).href;
  event.waitUntil((async()=>{
    const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const existing=windows.find(client=>new URL(client.url).origin===self.location.origin&&new URL(client.url).pathname==='/');
    if(existing){
      await existing.navigate(target);
      await existing.focus();
    }else await self.clients.openWindow(target);
  })());
});
