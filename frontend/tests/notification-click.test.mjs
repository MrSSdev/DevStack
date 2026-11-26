import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

// Match the other frontend tests: use the project's compiler rather than
// relying on native TypeScript loading (not available in every supported
// Node 20/22 build or when native type stripping is disabled).
const clientSource=readFileSync(new URL('../src/lib/notification-client.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(clientSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const { notificationClickId, notificationRouteHash, protocolLaunchHash, readNoticeOnClose, notificationClient, notificationDismissed } =
  await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'))

const base='https://devstack.test'
const mailId='M'.repeat(22)
const id='mail-'+mailId
const route='/#/mail/'+mailId
const source=readFileSync(new URL('../public/sw.js',import.meta.url),'utf8')

function worker(existing=false){
  const listeners=new Map()
  const calls=[]
  const client={url:base+'/#/dashboard',navigate:async url=>{calls.push(['navigate',url]);return client},focus:async()=>calls.push(['focus'])}
  const self={
    location:{origin:base},navigator:{},
    registration:{showNotification:async(title,options)=>calls.push(['show',title,options])},
    clients:{matchAll:async()=>existing?[client]:[],openWindow:async url=>calls.push(['open',url])},
    addEventListener:(type,handler)=>listeners.set(type,handler)
  }
  vm.runInNewContext(source,{self,URL,fetch:async(url,options)=>calls.push(['fetch',url,options])})
  return {calls,hasListener:type=>listeners.has(type),dispatch:async(type,event)=>{
    let done
    listeners.get(type)({...event,waitUntil:p=>{done=p}})
    await done
  }}
}

test('Web Push listener is retired but an existing notification can still be opened',async()=>{
  const w=worker()
  assert.equal(w.hasListener('push'),false)
  let closed=false
  await w.dispatch('notificationclick',{notification:{data:{route,id},close:()=>{closed=true}}})
  assert.equal(closed,true)
  assert.deepEqual(w.calls,[['open',base+route+'?notice='+id]])
})

test('existing window is focused and older notifications recover the ID from the tag',async()=>{
  const w=worker(true)
  await w.dispatch('notificationclick',{notification:{tag:'devstack-'+id,data:{route},close(){}}})
  assert.deepEqual(w.calls,[['navigate',base+route+'?notice='+id],['focus']])
})

test('invalid routes and IDs never navigate outside DevStack or create a read marker',async()=>{
  const w=worker()
  await w.dispatch('notificationclick',{notification:{tag:'devstack-invalid',data:{route:'https://evil.invalid/',id:'invalid'},close(){}}})
  assert.deepEqual(w.calls,[['open',base+'/#/dashboard']])
})

test('fragment parser keeps the normal route and validates the complete journal ID',()=>{
  const hash=route.slice(1)+'?notice='+id
  assert.equal(notificationClickId(hash),id)
  assert.equal(notificationRouteHash(hash),route.slice(1))
  assert.equal(notificationClickId('#/mail/'+mailId+'?notice=invalid'),undefined)
  assert.equal(notificationClickId('#/unknown?notice='+id),undefined)
  assert.equal(notificationClickId(hash+'&other=1'),undefined)
  assert.equal(notificationClickId('#/mail/'+mailId),undefined)
})

test('manifest protocol launches normalize to the existing route and exact journal marker',()=>{
  const manifest=JSON.parse(readFileSync(new URL('../public/manifest.webmanifest',import.meta.url),'utf8'))
  assert.deepEqual(manifest.protocol_handlers,[{protocol:'web+devstack',url:'/#/protocol?uri=%s'}])
  const launch=uri=>manifest.protocol_handlers[0].url.replace('%s',encodeURIComponent(uri)).slice(1)
  assert.equal(protocolLaunchHash(launch('web+devstack://open/mail')),'#/mail')
  assert.equal(protocolLaunchHash(launch('web+devstack://open/new')),'#/new')
  assert.equal(protocolLaunchHash(launch('web+devstack://open/projects')),'#/projects')
  assert.equal(protocolLaunchHash(launch('web+devstack://open/new?notice='+id)),'#/dashboard')
  assert.equal(protocolLaunchHash(launch('web+devstack://open/mail/'+mailId+'?notice='+id)),route.slice(1)+'?notice='+id)
  assert.equal(protocolLaunchHash(launch('web+devstack://open/projects?notice=op-'+'a'.repeat(32))),'#/projects?notice=op-'+'a'.repeat(32))
  assert.equal(protocolLaunchHash('#/mail'),undefined)
  for(const uri of [
    'https://evil.invalid/',
    'web+devstack://evil.invalid/mail',
    'web+devstack://open//evil.invalid/',
    'web+devstack://open/../mail',
    'web+devstack://open/mail?notice=invalid',
    'web+devstack://open/mail/'+mailId+'?notice=mail-'+'N'.repeat(22),
    'web+devstack://open/mail/'+mailId+'?notice='+id+'&extra=1',
    'web+devstack://open/mail\n',
    'web+devstack://open/mail?url=https://evil.invalid/',
    'web+devstack://open/'+ 'a'.repeat(600)
  ])assert.equal(protocolLaunchHash(launch(uri)),'#/dashboard',uri)
  assert.equal(protocolLaunchHash('#/protocol?uri=%zz'),'#/dashboard')
})

test('worker leaves only the named client with the existing session and CSRF header',async()=>{
  const w=worker()
  const client='c'.repeat(32)
  const csrf='a'.repeat(64)
  const data={type:'devstack:notification-leave',client,csrf,url:'https://example.invalid/'}
  const event={data,origin:base,source:{type:'window'}}
  await w.dispatch('message',{...event,origin:'https://example.invalid'})
  await w.dispatch('message',{...event,source:{type:'worker'}})
  await w.dispatch('message',{...event,data:{...data,csrf:'invalid'}})
  await w.dispatch('message',{...event,data:{...data,client:'invalid'}})
  assert.deepEqual(w.calls,[])
  await w.dispatch('message',event)
  const [[operation,url,options]]=w.calls
  assert.equal(operation,'fetch')
  assert.equal(url,'/api/notifications/leave')
  assert.equal(options.method,'POST')
  assert.equal(options.credentials,'same-origin')
  assert.equal(options.headers['X-CSRF-Token'],csrf)
  assert.deepEqual(JSON.parse(options.body),{client})
})

test('closing a visible terminal toast emits one exact event, never read all',()=>{
  const events=[]
  const saved=Object.fromEntries(['window','document','CustomEvent'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]))
  const documentFixture={visibilityState:'visible'}
  try{
    Object.defineProperty(globalThis,'window',{configurable:true,value:{dispatchEvent:event=>events.push(event)}})
    Object.defineProperty(globalThis,'document',{configurable:true,value:documentFixture})
    Object.defineProperty(globalThis,'CustomEvent',{configurable:true,value:class{constructor(type,options){this.type=type;this.detail=options.detail}}})
    const close=readNoticeOnClose(id)
    assert.deepEqual(events,[])
    close()
    close()
    assert.equal(events.length,1)
    assert.equal(events[0].type,notificationDismissed)
    assert.deepEqual(events[0].detail,{id,client:notificationClient})
    assert.equal(readNoticeOnClose('invalid'),undefined)
    const stale=readNoticeOnClose(id,'0'.repeat(32))
    stale()
    documentFixture.visibilityState='hidden'
    const hidden=readNoticeOnClose('op-'+'a'.repeat(32))
    hidden()
    documentFixture.visibilityState='visible'
    hidden()
    assert.equal(events.length,1)
  }finally{
    for(const [key,descriptor] of Object.entries(saved)){
      if(descriptor)Object.defineProperty(globalThis,key,descriptor)
      else delete globalThis[key]
    }
  }
})
