// Adapted from shadcn/ui's Base UI toast registry component (MIT).
// https://ui.shadcn.com/docs/components/base/toast
import { useEffect, useState, type ReactNode } from 'react'
import { Toast as ToastPrimitive } from '@base-ui/react/toast'
import { CSPProvider } from '@base-ui/react/csp-provider'
import { CircleAlert, CircleCheck, Info, RefreshCw, TriangleAlert, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { readNoticeOnClose } from '@/lib/notification-client'

export const toast = ToastPrimitive.createToastManager()

// A direct API operation starts with a local loading ID, then receives the
// exact persistent op-ID from its response (including error responses).
// Never match by title, timing, or whichever notice was created last.
const operationClosures=new Map<string,()=>void>()
export function bindOperationToast(toastId:string,id:unknown,client:string){
  if(typeof id!=='string'||!/^op-[a-f0-9]{32}$/.test(id))return
  const close=readNoticeOnClose(id,client)
  if(close)operationClosures.set(toastId,close)
}
function terminalClosure(id?:string){
  if(!id)return undefined
  const close=operationClosures.get(id)
  operationClosures.delete(id)
  return close
}

export function beginNotice(title:string, description?:string) {
  return toast.add({title,description,type:'loading',timeout:0})
}
export function notify(title:string,id?:string) {
  return toast.add({id,title,description:undefined,type:'success',timeout:6000,onClose:terminalClosure(id)})
}
export function inform(title:string,id?:string,description?:string) {
  return toast.add({id,title,description,type:'info',timeout:9000,onClose:terminalClosure(id)})
}
export function fail(reason:unknown,id?:string,fallback='Nieznany błąd.') {
  return toast.add({id,title:reason instanceof Error?reason.message:typeof reason==='string'?reason:fallback,
    description:undefined,type:'error',timeout:12000,onClose:terminalClosure(id)})
}

function ToastIcon({type}:{type?:string}) {
  const Icon=type==='success'?CircleCheck:type==='error'?CircleAlert:type==='warning'?TriangleAlert:type==='loading'?RefreshCw:Info
  return <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0',
    type==='success'?'text-emerald-600 dark:text-emerald-400':type==='error'?'text-destructive':type==='warning'?'text-amber-600 dark:text-amber-400':'text-muted-foreground',
    type==='loading'&&'animate-spin motion-reduce:animate-none')}/>
}

// Native modal dialogs live in the browser top layer. A viewport outside the
// active dialog would be inert and invisible behind its backdrop, regardless
// of z-index. Move the same portal host, without remounting or losing timers.
function useToastHost() {
  const [host]=useState(()=>document.createElement('div'))
  useEffect(()=>{
    host.dataset.slot='toast-host'
    const sync=(closing?:HTMLDialogElement)=>{
      const dialogs=Array.from(document.querySelectorAll<HTMLDialogElement>('dialog:modal'))
        .filter(dialog=>dialog!==closing)
      const parent=dialogs.at(-1)??document.body
      if(host.parentElement===parent)return
      // appendChild disconnects and reconnects descendants, cancelling their
      // CSS transitions. Base UI 1.6 waits on Animation.finished to remove
      // ending toasts; a cancellation with no replacement animation can
      // leave an invisible ending item that prevents the stack collapsing.
      // Use the state-preserving DOM move for connected hosts in modern Edge
      // and Chromium. Initial attachment (or legacy browsers) uses appendChild.
      const destination=parent as HTMLElement & {moveBefore?:(node:Node,child:Node|null)=>void}
      if(host.isConnected&&typeof destination.moveBefore==='function'){
        destination.moveBefore(host,null)
      }else{
        parent.appendChild(host)
      }
    }
    // Closing a native dialog hides its descendants before the mutation
    // observer runs. A synchronous layout/focus update in that interval can
    // cancel even state-preserving moves' animations. Evacuate the live
    // host while the closing dialog is still rendered. beforetoggle does
    // not bubble; capture it at document level (including requestClose).
    const beforeToggle=(event:Event)=>{
      const closing=event.target
      if(closing instanceof HTMLDialogElement && (event as ToggleEvent).newState==='closed' && host.parentElement===closing){
        sync(closing)
      }
    }
    document.addEventListener('beforetoggle',beforeToggle,true)
    sync()
    const observer=new MutationObserver(()=>sync())
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['open']})
    return ()=>{
      observer.disconnect()
      document.removeEventListener('beforetoggle',beforeToggle,true)
      host.remove()
    }
  },[host])
  return host
}

function ToastList(){
  const {toasts}=ToastPrimitive.useToastManager()
  const frontmost=toasts.find(item=>item.transitionStatus!=='ending')?.id
  return <ToastPrimitive.Viewport data-slot="toast-viewport" aria-label="Powiadomienia (F6)"
    className="outline-none focus-visible:ring-2 focus-visible:ring-ring">
    {toasts.map(item=><ToastPrimitive.Root key={item.id} toast={item} data-slot="toast" data-toast-type={item.type}
      swipeDirection={item.type==='loading'?[]:['right']}
      onKeyDownCapture={e=>{if(item.type==='loading'&&e.key==='Escape'){e.preventDefault();e.stopPropagation()}}}
      className="rounded-xl border bg-popover text-popover-foreground shadow-lg outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <ToastPrimitive.Content data-slot="toast-content" className="flex items-center gap-3 p-3">
        <ToastIcon type={item.type}/>
        <div className="min-w-0 flex-1 space-y-1">
          <ToastPrimitive.Title className="wrap-anywhere text-sm font-medium leading-snug"/>
          {item.description&&<ToastPrimitive.Description className="whitespace-pre-line wrap-anywhere text-xs leading-relaxed text-muted-foreground"/>}
        </div>
        {item.actionProps?.children&&<ToastPrimitive.Action data-slot="toast-action" render={<Button variant="outline" size="sm"/>} className="shrink-0"/>}
        {/* Keep the visible frontmost close accessible without hovering.
            Behind it, let Base UI expose controls when the stack expands. */}
        {item.type!=='loading'&&<ToastPrimitive.Close data-slot="toast-close" render={<Button variant="ghost" size="icon"/>} aria-label="Zamknij powiadomienie"
          {...(frontmost===item.id?{'aria-hidden':false}:{})}
          className="size-7 shrink-0 border-transparent bg-transparent text-muted-foreground hover:bg-transparent hover:text-foreground"><X aria-hidden="true"/></ToastPrimitive.Close>}
      </ToastPrimitive.Content>
    </ToastPrimitive.Root>)}
  </ToastPrimitive.Viewport>
}

export function Toaster({children}:{children:ReactNode}){
  const host=useToastHost()
  return <CSPProvider disableStyleElements>
    <ToastPrimitive.Provider toastManager={toast} timeout={6000} limit={Number.MAX_SAFE_INTEGER}>
      {children}
      <ToastPrimitive.Portal container={host}><ToastList/></ToastPrimitive.Portal>
    </ToastPrimitive.Provider>
  </CSPProvider>
}
