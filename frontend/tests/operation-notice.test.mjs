import { readFileSync, readdirSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

const root=new URL('../src/',import.meta.url)
const apiSource=readFileSync(new URL('lib/api.ts',root),'utf8')
const compiled=ts.transpileModule(apiSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText
const notificationId='op-'+'a'.repeat(32)
const jobId='b'.repeat(32)
function fixture(envelope,status=200){
  const events=[]
  const exports={}
  vm.runInNewContext(compiled,{
    exports,
    require(name){
      if(name==='@/components/ui/toast')return {
        toast:{close:id=>events.push(['close',id])},
        beginNotice:(...args)=>{events.push(['begin',...args]);return 'pending'},
        bindOperationToast:(...args)=>events.push(['bind',...args]),
        notify:(...args)=>events.push(['success',...args]),
        inform:(...args)=>events.push(['info',...args]),
        fail:(reason,id)=>events.push(['error',reason?.message??reason,id])
      }
      if(name==='@/lib/notification-client')return {
        notificationClient:'client',notificationChange:'changed',
        acknowledgeOperation:(...args)=>events.push(['ack',...args])
      }
      throw new Error('Unexpected import '+name)
    },
    fetch:async(path,options)=>{events.push(['fetch',path,options.method]);return {ok:status<300,status,json:async()=>envelope}},
    crypto:{randomUUID:()=> 'pending'},
    Event:class {constructor(type){this.type=type}},
    window:{dispatchEvent:event=>events.push(['event',event.type])}
  })
  return {api:exports,events}
}
test('successful operation binds one persistent ID before presenting the terminal toast',async()=>{
  const {api,events}=fixture({ok:true,result:{message:'saved',notification_id:notificationId}})
  await api.runOperation('/api/ca/wsl','csrf',{}, {title:'working',onResult:result=>{events.push(['state',result.message])}})
  assert.deepEqual(events,[
    ['begin','working',undefined],['fetch','/api/ca/wsl','POST'],
    ['bind','pending',notificationId,'client'],['ack',notificationId,'csrf','client'],
    ['state','saved'],['success','saved','pending']
  ])
})
test('real API failure binds the same exact journal ID and never emits success',async()=>{
  const {api,events}=fixture({ok:false,error:'failed',notification_id:notificationId},422)
  assert.equal(await api.runOperation('/api/ca/wsl','csrf',{}, {title:'working'}),undefined)
  assert.deepEqual(events.slice(-3),[
    ['bind','pending',notificationId,'client'],['ack',notificationId,'csrf','client'],['error','failed','pending']
  ])
})
test('service acceptance stays informational and a job hands off the original loading toast',async()=>{
  const service=fixture({ok:true,result:{accepted:true,notification_id:notificationId}})
  await service.api.runOperation('/api/services/control','csrf',{}, {title:'working',onResult:()=>({kind:'info',title:'accepted',description:'later'})})
  assert.deepEqual(service.events.at(-1),['info','accepted','pending','later'])
  const job=fixture({ok:true,result:{job_id:jobId,notification_id:'job-'+jobId}})
  await job.api.runOperation('/api/engines/laravel','csrf',{}, {title:'install',onJob:(_,id,accepted)=>job.events.push(['job',id,accepted])})
  assert.ok(job.events.some(e=>e[0]==='event'))
  assert.deepEqual(job.events.at(-1),['job','pending',jobId])
  assert.ok(!job.events.some(e=>e[0]==='success'||e[0]==='info'||e[0]==='error'))
})
test('journal outage does not turn a successful mutation into a retry',async()=>{
  const {api,events}=fixture({ok:true,result:{message:'saved'}})
  await api.runOperation('/api/preferences','csrf',{}, {title:'working',loading:false})
  assert.deepEqual(events.at(-1),['success','saved','pending'])
  assert.ok(!events.some(e=>e[0]==='error'))
})
test('raw POST is restricted to explicit local exceptions',async()=>{
  const {api,events}=fixture({ok:true,result:[]})
  await assert.rejects(api.request('POST','/api/ca/wsl','csrf',{}))
  await assert.rejects(api.request('POST','/api/new-feature','csrf',{}))
  assert.equal(events.length,0)
  await api.request('POST','/api/notifications/poll','csrf',{})
  assert.deepEqual(events,[['fetch','/api/notifications/poll','POST']])
  const excluded=fixture({ok:true,result:[]})
  await excluded.api.runOperation('/api/reveal','csrf',{}, {title:'invalid'})
  assert.ok(!excluded.events.some(e=>e[0]==='fetch'))
})
function sources(url){
  return readdirSync(url,{withFileTypes:true}).flatMap(entry=>{
    const child=new URL(entry.name+(entry.isDirectory()?'/':''),url)
    return entry.isDirectory()?sources(child):/\.(?:ts|tsx)$/.test(entry.name)?[child]:[]
  })
}
test('all frontend POST calls use the operation helper or an explicit local exception',()=>{
  const {api}=fixture({})
  const server=readFileSync(new URL('../../backend/notifications.php',import.meta.url),'utf8')
  const policy=server.match(/public const POST_LOCAL = \[([\s\S]*?)\];/)?.[1]
  assert.ok(policy,'server local policy exists')
  const local=[...policy.matchAll(/'(\/api\/[^']+)'\s*=>/g)].map(m=>m[1]).sort()
  assert.deepEqual([...api.localPostPaths].sort(),local)
  for(const file of sources(root)){
    const rel=file.href.slice(root.href.length)
    const ast=ts.createSourceFile(rel,readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,rel.endsWith('.tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS)
    function visit(node){
      if(ts.isCallExpression(node)&&ts.isIdentifier(node.expression)){
        const name=node.expression.text
        if(rel!=='lib/api.ts'&&name==='request'){
          const [method,path]=node.arguments
          assert.ok(method&&ts.isStringLiteral(method),rel+': request method must be explicit')
          if(method.text==='POST')assert.ok(path&&ts.isStringLiteral(path)&&local.includes(path.text),rel+': journaled POST must use runOperation')
          else assert.equal(method.text,'GET',rel+': unsupported request method')
        }
        if(name==='fetch')assert.ok(['lib/api.ts','lib/notification-client.ts'].includes(rel),rel+': use the shared API transport')
        if(['bindOperationToast','acknowledgeOperation'].includes(name))assert.equal(rel,'lib/api.ts',rel+': operation acknowledgement belongs to runOperation')
      }
      ts.forEachChild(node,visit)
    }
    visit(ast)
  }
})
