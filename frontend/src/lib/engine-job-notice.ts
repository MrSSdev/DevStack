// Only the identity and terminal status of the last acknowledged job are
// kept in this browser. The daemon retains the complete authoritative result.
type NoticeJob = { id:string; state:string }
type NoticeStorage = Pick<Storage, 'getItem'|'setItem'>
type StorageProvider = () => NoticeStorage
export const JOB_NOTICE_KEY = 'devstack.engine-job-notice.v1'
const browserStorage:StorageProvider = () => window.localStorage

function completedKey(job:NoticeJob|null|undefined):string {
  return job && /^[a-f0-9]{32}$/.test(job.id) && (job.state==='succeeded'||job.state==='failed')
    ? job.state+':'+job.id : ''
}

export function readAcknowledgedJob(storage:StorageProvider=browserStorage):string {
  try {
    const value=storage().getItem(JOB_NOTICE_KEY)??''
    return /^(succeeded|failed):[a-f0-9]{32}$/.test(value)?value:''
  }catch{
    // Storage may be blocked (privacy settings, private browsing, quota).
    return ''
  }
}

export function showJobNotice(job:NoticeJob|null|undefined, acknowledged:string):boolean {
  if(!job)return false
  const key=completedKey(job)
  return !key||key!==acknowledged
}

export function acknowledgeJob(job:NoticeJob|null|undefined, storage:StorageProvider=browserStorage):string {
  const key=completedKey(job)
  if(!key)return ''
  try{storage().setItem(JOB_NOTICE_KEY,key)}catch{/* in-memory dismissal still works */}
  return key
}

export function rememberVisibleSuccess(job:NoticeJob|null|undefined, visible:boolean, storage:StorageProvider=browserStorage):void {
  // Do not silently acknowledge a failure or a result received in a hidden tab.
  if(visible&&job?.state==='succeeded')acknowledgeJob(job,storage)
}
