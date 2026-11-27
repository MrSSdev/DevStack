import { toast, beginNotice, bindOperationToast, notify, inform, fail } from '@/components/ui/toast'
import { acknowledgeOperation, notificationClient, notificationChange } from '@/lib/notification-client'

type Envelope<T> = { notification_id?:string; ok: boolean; result?: T; error?: string; authenticated?: boolean; csrf?: string }

// This explicit exception list mirrors DevStackNotices::POST_LOCAL;
// the contract test compares it with the server catalogue. All other
// authenticated POSTs must use runOperation, never a naked request.
export const localPostPaths = [
  '/api/login',
  '/api/logout',
  '/api/reveal',
  '/api/notifications/poll',
  '/api/notifications/leave',
  '/api/notifications/ack',
  '/api/notifications/manage',
  '/api/notifications/categories',
  '/api/notifications/unsubscribe',
  '/api/notifications/push-key',
  '/api/notifications/subscribe',
] as const
const localPosts=new Set<string>(localPostPaths)

async function send<T>(method: 'GET'|'POST', path: string, csrf?: string, data?: unknown, operationToastId?:string, signal?:AbortSignal): Promise<T> {
  const client=notificationClient
  const response = await fetch(path, {
    method, credentials: 'same-origin', cache: 'no-store', signal,
    headers: { 'X-DevStack-Client':client, ...(data ? {'Content-Type':'application/json'} : {}), ...(method === 'POST' ? {'X-CSRF-Token':csrf ?? ''} : {}) },
    ...(data ? {body:JSON.stringify(data)} : {}),
  })
  let body: Envelope<T>
  try { body = await response.json() as Envelope<T> }
  catch { throw new Error(`Nieprawidłowa odpowiedź serwera (HTTP ${response.status}).`) }
  if(method==='POST'&&csrf&&operationToastId){
    const result=body.result as {notification_id?:string}|undefined
    const id=result?.notification_id??body.notification_id
    bindOperationToast(operationToastId,id,client)
    acknowledgeOperation(id,csrf,client)
    if(typeof id==='string'&&id.startsWith('job-'))window.dispatchEvent(new Event(notificationChange))
  }
  if (!response.ok || !body.ok) throw new Error(body.error || `Błąd HTTP ${response.status}.`)
  return (body.result ?? body) as T
}

// Reads and intentionally transient/session/notification operations only.
export async function request<T>(method:'GET'|'POST',path:string,csrf?:string,data?:unknown,signal?:AbortSignal):Promise<T>{
  if(method==='POST'&&!localPosts.has(path))throw new Error('Operacja wymaga wspólnego mechanizmu powiadomień.')
  return send<T>(method,path,csrf,data,undefined,signal)
}

type Presentation=string|{kind:'info';title:string;description?:string}|void
type OperationOptions<T>={
  title:string
  description?:string
  loading?:boolean
  onResult?:(result:T)=>Presentation|Promise<Presentation>
  onJob?:(result:T,toastId:string,jobId:string)=>void
  onError?:(reason:unknown)=>void
  formatError?:(reason:unknown)=>unknown
  errorFallback?:string
}

// The only foreground entry point for journaled operations. One loading
// toast, automatic exact-ID binding/acknowledgement on success AND failure,
// and one terminal presentation. Installer acceptance hands the same
// toast to the existing stable job lifecycle instead of completing it here.
// A failed journal write does not turn a completed mutation into a retry.
export async function runOperation<T=unknown>(path:string,csrf:string,data:unknown,options:OperationOptions<T>):Promise<T|undefined>{
  const pending=options.loading===false?crypto.randomUUID():beginNotice(options.title,options.description)
  try{
    if(localPosts.has(path))throw new Error('Ta operacja jest wyłączona z historii powiadomień.')
    const result=await send<T>('POST',path,csrf,data,pending)
    const fields=result as {message?:unknown;job_id?:unknown}|null
    const job=typeof fields?.job_id==='string'&&/^[a-f0-9]{32}$/.test(fields.job_id)?fields.job_id:undefined
    if(job){
      if(options.onJob)options.onJob(result,pending,job)
      else toast.close(pending) // existing job observer owns the presentation
    }
    const presentation=await options.onResult?.(result)
    if(!job){
      if(typeof presentation==='object'&&presentation?.kind==='info')inform(presentation.title,pending,presentation.description)
      else notify(typeof presentation==='string'?presentation:typeof fields?.message==='string'?fields.message:'Operacja zakończona.',pending)
    }
    return result
  }catch(e){
    options.onError?.(e)
    fail(options.formatError?options.formatError(e):e,pending,options.errorFallback)
    return undefined
  }
}
