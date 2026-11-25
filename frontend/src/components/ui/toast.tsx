// Adapted from shadcn/ui's Base UI toast registry component (MIT).
// https://ui.shadcn.com/docs/components/base/toast
import { useEffect, useState, type ReactNode } from 'react'
import { Toast as ToastPrimitive } from '@base-ui/react/toast'
import { CSPProvider } from '@base-ui/react/csp-provider'
import { CircleAlert, CircleCheck, Info, RefreshCw, TriangleAlert, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export const toast = ToastPrimitive.createToastManager()

export function beginNotice(title:string, description?:string) {
  return toast.add({title,description,type:'loading',timeout:0})
}
export function notify(title:string,id?:string) {
  return toast.add({id,title,description:undefined,type:'success',timeout:6000})
}
export function inform(title:string,id?:string,description?:string) {
  return toast.add({id,title,description,type:'info',timeout:9000})
}
export function fail(reason:unknown,id?:string,fallback='Nieznany błąd.') {
  return toast.add({id,title:reason instanceof Error?reason.message:typeof reason==='string'?reason:fallback,
    description:undefined,type:'error',timeout:12000})
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
    const sync=()=>{
      const dialogs=document.querySelectorAll<HTMLDialogElement>('dialog:modal')
      const parent=dialogs.item(dialogs.length-1)??document.body
      if(host.parentElement!==parent)parent.appendChild(host)
    }
    sync()
    const observer=new MutationObserver(sync)
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['open']})
    return ()=>{observer.disconnect();host.remove()}
  },[host])
  return host
}

function ToastList(){
  const {toasts}=ToastPrimitive.useToastManager()
  return <ToastPrimitive.Viewport data-slot="toast-viewport" aria-label="Powiadomienia (F6)"
    className={cn('fixed inset-x-3 bottom-3 z-[100] mx-auto flex max-h-[calc(100dvh-1.5rem)] max-w-sm flex-col-reverse gap-2 overflow-y-auto overscroll-contain rounded-xl outline-none sm:right-4 sm:bottom-4 sm:left-auto sm:mx-0 sm:w-96 sm:max-h-[calc(100dvh-2rem)] focus-visible:ring-2 focus-visible:ring-ring',
      toasts.length?'pointer-events-auto p-1':'pointer-events-none')}>
    {toasts.map(item=><ToastPrimitive.Root key={item.id} toast={item} data-slot="toast"
      swipeDirection={['right']} className="relative shrink-0 rounded-lg border bg-popover text-popover-foreground shadow-lg outline-none transition-[transform,opacity] duration-200 [transform:translateX(var(--toast-swipe-movement-x,0px))] data-starting-style:translate-x-6 data-starting-style:opacity-0 data-ending-style:translate-x-6 data-ending-style:opacity-0 motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring">
      <ToastPrimitive.Content className="flex items-start gap-2.5 p-3">
        <ToastIcon type={item.type}/>
        <div className="min-w-0 flex-1 space-y-1">
          <ToastPrimitive.Title className="break-words text-sm font-medium leading-snug"/>
          {item.description&&<ToastPrimitive.Description className="whitespace-pre-line break-words text-xs leading-relaxed text-muted-foreground"/>}
          {item.actionProps?.children&&<ToastPrimitive.Action render={<Button variant="outline" size="sm"/>} className="mt-1 max-w-full"/>}
        </div>
        <ToastPrimitive.Close render={<Button variant="ghost" size="icon"/>} aria-label="Zamknij powiadomienie" className="-mr-1 -mt-1 size-7 shrink-0"><X aria-hidden="true"/></ToastPrimitive.Close>
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
