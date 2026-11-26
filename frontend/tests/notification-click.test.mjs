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
const { notificationClickId, notificationRouteHash } =
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
  return {calls,dispatch:async(type,event)=>{
    let done
    listeners.get(type)({...event,waitUntil:p=>{done=p}})
    await done
  }}
}

test('push carries the exact journal ID and cold click opens the mail with its read marker',async()=>{
  const w=worker()
  await w.dispatch('push',{data:{json:()=>({id,category:'mail',kind:'info'})}})
  const [,title,options]=w.calls[0]
  assert.equal(title,'Nowa poczta w DevStack')
  assert.equal(options.data.id,id)
  let closed=false
  await w.dispatch('notificationclick',{notification:{...options,close:()=>{closed=true}}})
  assert.equal(closed,true)
  assert.deepEqual(w.calls[1],['open',base+route+'?notice='+id])
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
