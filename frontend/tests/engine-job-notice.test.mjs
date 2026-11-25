import assert from 'node:assert/strict'
import test from 'node:test'
import { JOB_NOTICE_KEY, readAcknowledgedJob, showJobNotice, acknowledgeJob, rememberVisibleSuccess } from '../src/lib/engine-job-notice.ts'

const job=(state,id='a'.repeat(32))=>({id,state})
function fakeStorage(){
  const values=new Map()
  return ()=>({getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)})
}
test('visible success is shown once across reloads, but stays visible in the current mount',()=>{
  const storage=fakeStorage(), completed=job('succeeded')
  const initial=readAcknowledgedJob(storage)
  assert.equal(showJobNotice(completed,initial),true)
  rememberVisibleSuccess(completed,true,storage)
  assert.equal(showJobNotice(completed,initial),true)
  assert.equal(showJobNotice(completed,readAcknowledgedJob(storage)),false)
  assert.equal(showJobNotice(job('succeeded','b'.repeat(32)),readAcknowledgedJob(storage)),true)
})
test('active jobs are always visible; hidden-tab success waits for a visible view',()=>{
  const storage=fakeStorage()
  acknowledgeJob(job('succeeded'),storage)
  assert.equal(showJobNotice(job('scaffolding'),readAcknowledgedJob(storage)),true)
  const next=job('succeeded','b'.repeat(32))
  rememberVisibleSuccess(next,false,storage)
  assert.equal(showJobNotice(next,readAcknowledgedJob(storage)),true)
  rememberVisibleSuccess(next,true,storage)
  assert.equal(showJobNotice(next,readAcknowledgedJob(storage)),false)
  assert.equal(acknowledgeJob(job('checking'),storage),'')
})
test('failures remain visible until explicitly dismissed; dismissal survives reload',()=>{
  const storage=fakeStorage(), failed=job('failed')
  rememberVisibleSuccess(failed,true,storage)
  assert.equal(showJobNotice(failed,readAcknowledgedJob(storage)),true)
  const dismissed=acknowledgeJob(failed,storage)
  assert.equal(showJobNotice(failed,dismissed),false)
  assert.equal(showJobNotice(failed,readAcknowledgedJob(storage)),false)
  assert.equal(showJobNotice(job('failed','b'.repeat(32)),readAcknowledgedJob(storage)),true)
})
test('unavailable or malformed storage does not break the panel or active jobs',()=>{
  const blocked=()=>{throw new Error('blocked')}
  assert.equal(readAcknowledgedJob(blocked),'')
  const dismissed=acknowledgeJob(job('failed'),blocked)
  assert.equal(showJobNotice(job('failed'),dismissed),false)
  assert.equal(showJobNotice(job('checking'),dismissed),true)
  const storage=fakeStorage()
  storage().setItem(JOB_NOTICE_KEY,'invalid')
  assert.equal(readAcknowledgedJob(storage),'')
  assert.equal(showJobNotice(null,''),false)
})
