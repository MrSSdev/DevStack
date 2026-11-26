import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import { notificationClickId, notificationRouteHash } from '../src/lib/notification-client.ts'

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
  vm.runInNewContext(source,{self,URL})
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
