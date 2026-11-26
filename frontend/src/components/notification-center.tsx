import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Bell, CheckCheck, CircleAlert, CircleCheck, FolderCode, History, Info, Mail, RefreshCw, Settings2, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { toast, fail } from '@/components/ui/toast'
import { cn } from '@/lib/utils'
import { acknowledgeRetiredNotificationClients, consumeNotificationClick, disablePush, emptyNoticeState, enablePush, isNoticeRoute, leaveNotifications, notificationCategories, notificationChange, notificationClickId, notificationClient, renewNotificationClient, retiredNotificationClients, updateAppBadge, type Notice, type NoticeRequest, type NoticeState } from '@/lib/notification-client'

export function useNotificationCenter(authenticated:boolean,csrf:string,request:NoticeRequest,navigate:(route:string)=>void){
  const [state,setState]=useState<NoticeState>(emptyNoticeState)
  const [available,setAvailable]=useState(true)
  const [working,setWorking]=useState(false)
  const inFlight=useRef(false)
  const queued=useRef(false)
  const generation=useRef(0)
  const active=useRef(false)
  const suspended=useRef(false)
  const syncRef=useRef<()=>Promise<void>>(async()=>{})
  const pendingOpened=useRef(new Set<string>())
  const navigateRef=useRef(navigate)
  navigateRef.current=navigate
  const show=(item:Notice)=>{
    toast.add({id:item.id,title:item.title,description:item.description,type:item.kind,
      timeout:item.kind==='error'?12000:9000,
      actionProps:isNoticeRoute(item.route)?{children:'Otwórz',onClick:()=>{
        // Use the same exact-event acknowledgement as a Windows click. The
        // server marks it read before returning history; never read all.
        pendingOpened.current.add(item.id)
        navigateRef.current(item.route);toast.close(item.id);void syncRef.current()
      }}:undefined})
  }
  const sync=useCallback(async()=>{
    if(!active.current||suspended.current||!csrf)return
    const clicked=notificationClickId()
    if(clicked)pendingOpened.current.add(clicked)
    if(inFlight.current){queued.current=true;return}
    inFlight.current=true
    const version=generation.current
    const opened=pendingOpened.current.values().next().value
    const client=notificationClient
    const retired=retiredNotificationClients().filter(id=>id!==client)
    try{
      const result=await request<NoticeState>('POST','/api/notifications/poll',csrf,{client,...(opened?{opened}:{}),...(retired.length?{retired}:{})})
      if(!active.current||suspended.current||version!==generation.current)return
      acknowledgeRetiredNotificationClients(retired)
      if(opened){
        pendingOpened.current.delete(opened)
        consumeNotificationClick(opened)
        toast.close(opened)
        if(pendingOpened.current.size)queued.current=true
      }
      setState(result);setAvailable(true)
      for(const item of result.toast)show(item)
    }catch{
      if(active.current&&!suspended.current&&version===generation.current)setAvailable(false)
    }finally{
      inFlight.current=false
      if(queued.current){queued.current=false;if(active.current&&!suspended.current)void syncRef.current()}
    }
  },[csrf,request])
  syncRef.current=sync
  useEffect(()=>{
    active.current=authenticated
    if(!authenticated){suspended.current=false;generation.current++;pendingOpened.current.clear();setState(emptyNoticeState);void updateAppBadge(0);return}
    let timer:number|undefined
    const refresh=()=>{void syncRef.current()}
    const start=()=>{if(timer===undefined)timer=window.setInterval(refresh,5000)}
    const stop=()=>{if(timer!==undefined){window.clearInterval(timer);timer=undefined}}
    const leave=()=>{
      if(suspended.current)return
      // Stop before sending leave. An in-flight response must not schedule
      // another heartbeat or update the closed page.
      suspended.current=true;generation.current++;queued.current=false;stop()
      leaveNotifications(csrf,notificationClient)
    }
    const restore=()=>{
      if(suspended.current){
        // A BFCache document resumes with a new lifetime/ID. Its old keepalive
        // leave and any old HTTP requests can arrive in either order.
        renewNotificationClient();suspended.current=false;start()
      }
      refresh()
    }
    // Hidden/minimized is not closed. Keep the existing interval, but don't
    // start an extra poll as visibility changes to hidden during pagehide.
    const visibility=()=>{if(document.visibilityState==='visible')refresh()}
    window.addEventListener('focus',refresh)
    window.addEventListener('pageshow',restore)
    window.addEventListener('hashchange',refresh)
    window.addEventListener('pagehide',leave)
    window.addEventListener(notificationChange,refresh)
    document.addEventListener('visibilitychange',visibility)
    if(!suspended.current){start();refresh()}
    return ()=>{
      active.current=false;generation.current++;queued.current=false;stop()
      window.removeEventListener('focus',refresh)
      window.removeEventListener('pageshow',restore)
      window.removeEventListener('hashchange',refresh)
      window.removeEventListener('pagehide',leave)
      window.removeEventListener(notificationChange,refresh)
      document.removeEventListener('visibilitychange',visibility)
    }
  },[authenticated,csrf,sync])
  const unread=state.items.filter(item=>item.unread).length
  useEffect(()=>{if(authenticated)void updateAppBadge(unread)},[authenticated,unread])

  const manage=async(action:'delete'|'clear'|'read'|'read-all',id?:string)=>{
    if(working)return false
    setWorking(true)
    try{
      await request('POST','/api/notifications/manage',csrf,{action,...(id?{id}:{})})
      generation.current++
      setState(previous=>({...previous,items:action==='clear'?[]:action==='delete'?previous.items.filter(n=>n.id!==id):
        previous.items.map(n=>action==='read-all'||n.id===id?{...n,unread:false}:n)}))
      // Deleting a history record is not cancelling the underlying operation.
      // A running installer toast remains intact through its terminal state.
      if(action==='delete'&&id&&!id.startsWith('job-'))toast.close(id)
      if(action==='clear')for(const item of state.items)if(!item.id.startsWith('job-'))toast.close(item.id)
      await sync()
      return true
    }catch(e){fail(e,'notification-history-error');return false}
    finally{setWorking(false)}
  }
  const changePush=async(enabled:boolean)=>{
    if(working)return
    setWorking(true)
    try{if(enabled)await enablePush(request,csrf);else await disablePush(request,csrf);await sync()}
    catch(e){fail(e,'notification-push-error')}
    finally{setWorking(false)}
  }
  const changeCategory=async(id:string,enabled:boolean)=>{
    if(working)return
    setWorking(true)
    try{
      const categories=enabled?[...state.push_categories,id]:state.push_categories.filter(v=>v!==id)
      await request('POST','/api/notifications/categories',csrf,{categories});await sync()
    }catch(e){fail(e,'notification-category-error')}
    finally{setWorking(false)}
  }
  return {state,available,unread,working,sync,manage,changePush,changeCategory}
}
export type NotificationModel=ReturnType<typeof useNotificationCenter>

function NoticeIcon({item}:{item:Notice}){
  const Icon=item.kind==='loading'?RefreshCw:item.kind==='error'?CircleAlert:item.category==='mail'?Mail:
    item.kind==='success'?CircleCheck:item.category==='project'||item.category==='installer'?FolderCode:
    item.category==='settings'?Settings2:Info
  return <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0',
    item.kind==='loading'?'animate-spin motion-reduce:animate-none text-muted-foreground':
    item.kind==='error'?'text-destructive':item.kind==='success'?'text-emerald-600 dark:text-emerald-400':'text-muted-foreground')}/>
}

export function NotificationCenter({model,navigate}:{model:NotificationModel;navigate:(route:string)=>void}){
  const id=useId()
  const panel=useRef<HTMLDivElement>(null)
  const trigger=useRef<HTMLButtonElement>(null)
  const [open,setOpen]=useState(false)
  useEffect(()=>{
    const el=panel.current
    if(!el)return
    const change=()=>{setOpen(el.matches(':popover-open'))}
    el.addEventListener('toggle',change)
    return ()=>el.removeEventListener('toggle',change)
  },[])
  const close=()=>{if(panel.current?.matches(':popover-open'))panel.current.hidePopover();trigger.current?.focus()}
  const openNotice=async(item:Notice)=>{
    if(await model.manage('read',item.id)){close();if(isNoticeRoute(item.route))navigate(item.route)}
  }
  return <>
    <Button ref={trigger} size="icon" variant="outline" className="relative" aria-label="Powiadomienia"
      aria-controls={id} aria-expanded={open} title={model.unread?'Powiadomienia: '+model.unread+' nieprzeczytanych':'Powiadomienia'}
      onClick={()=>{if(panel.current?.matches(':popover-open'))panel.current.hidePopover();else{panel.current?.showPopover();void model.sync()}}}>
      <Bell/>{model.unread>0&&<span className="absolute -right-1 -top-1 min-w-4 rounded-full border border-background bg-primary px-0.5 text-center text-[10px] font-semibold leading-4 text-primary-foreground" aria-hidden="true">{model.unread>99?'99+':model.unread}</span>}
    </Button>
    <div ref={panel} id={id} popover="auto" role="region" aria-label="Historia powiadomień"
      className="fixed inset-auto right-3 top-14 m-0 w-[min(25rem,calc(100vw-1.5rem))] max-h-[calc(100dvh-4.5rem)] overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-xl sm:right-5">
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <History className="size-4 shrink-0 text-muted-foreground"/>
        <h2 className="min-w-0 flex-1 text-sm font-semibold">Powiadomienia</h2>
        <span className="text-xs text-muted-foreground">{model.unread} nowych</span>
        <Button size="icon" variant="ghost" aria-label="Zamknij historię" onClick={close}><X/></Button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-1 border-b p-2">
        <Button variant="ghost" size="sm" disabled={!model.unread||model.working} onClick={()=>void model.manage('read-all')}><CheckCheck/>Odczytaj wszystkie</Button>
        <Button variant="outline" size="sm" disabled={!model.state.items.length||model.working} onClick={()=>void model.manage('clear')}><Trash2/>Wyczyść wszystko</Button>
      </div>
      {!model.available&&<p className="px-3 py-2 text-xs text-destructive" role="status">Historia chwilowo niedostępna.</p>}
      <div className="max-h-[max(8rem,calc(100dvh-12rem))] overflow-y-auto overscroll-contain divide-y">
        {model.state.items.length===0?<div className="space-y-1 px-4 py-8 text-center"><Bell className="mx-auto mb-2 size-6 text-muted-foreground"/><p className="text-sm font-medium">Brak powiadomień</p><p className="text-xs text-muted-foreground">Nowe zdarzenia pojawią się tutaj.</p></div>:
          model.state.items.map(item=><div key={item.id} data-notification-id={item.id} className={cn('flex min-w-0 items-start gap-1 px-2 py-1.5',item.unread&&'bg-secondary/40')}>
            <button type="button" className="flex min-w-0 flex-1 gap-2 rounded-md p-1.5 text-left hover:bg-secondary focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50" disabled={model.working} onClick={()=>void openNotice(item)}>
              <NoticeIcon item={item}/>
              <span className="min-w-0 flex-1 space-y-0.5">
                <span className="flex items-start gap-1.5"><span className={cn('min-w-0 flex-1 break-words text-sm',item.unread?'font-semibold':'font-medium')}>{item.title}</span>{item.unread&&<span className="mt-1.5 size-2 shrink-0 rounded-full bg-blue-500" aria-label="Nieprzeczytane"/>}</span>
                {item.description&&<span className="block break-words text-xs leading-relaxed text-muted-foreground">{item.description}</span>}
                <time className="block text-[11px] text-muted-foreground" dateTime={new Date(item.updated*1000).toISOString()}>{new Date(item.updated*1000).toLocaleString('pl-PL',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}</time>
              </span>
            </button>
            <Button variant="ghost" size="icon" className="mt-1 shrink-0" disabled={model.working} title="Usuń z historii" aria-label={'Usuń powiadomienie: '+item.title} onClick={()=>void model.manage('delete',item.id)}><Trash2/></Button>
          </div>)}
      </div>
      <div className="border-t px-3 py-2 text-xs text-muted-foreground">Usuwanie z historii nie usuwa poczty ani projektów.</div>
    </div>
  </>
}
export function NotificationSettings({model}:{model:NotificationModel}){
  const supported='Notification' in window&&'PushManager' in window&&'serviceWorker' in navigator
  const standalone=window.matchMedia('(display-mode: standalone)').matches
  return <div className="space-y-3">
    <Card><CardHeader><CardTitle>Aplikacja DevStack (PWA)</CardTitle><CardDescription>Własne okno i ikona w systemie Windows.</CardDescription></CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>{standalone?'DevStack działa w osobnym oknie aplikacji.':'W Microsoft Edge wybierz menu → Aplikacje → Zainstaluj tę witrynę jako aplikację. Możesz przypiąć DevStack do menu Start i paska zadań.'}</p>
        <p className="text-xs text-muted-foreground">PWA nie przechowuje poczty ani danych projektów w pamięci offline. Nie uruchamia Ubuntu automatycznie.</p>
      </CardContent></Card>
    <Card><CardHeader><CardTitle>Powiadomienia systemowe</CardTitle><CardDescription>Historia i toasty działają lokalnie, bez Web Push.</CardDescription></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between gap-3"><div className="min-w-0"><p className="text-sm font-medium">Web Push po zamknięciu panelu</p><p className="text-xs text-muted-foreground">{model.state.subscribed?'Włączone dla jednego profilu przeglądarki.':'Wyłączone.'}</p></div>
          <Button size="sm" variant={model.state.subscribed?'outline':'default'} disabled={model.working||(!supported&&!model.state.subscribed)} onClick={()=>void model.changePush(!model.state.subscribed)}>{model.state.subscribed?'Wyłącz':'Włącz Web Push'}</Button></div>
        <p className="text-xs text-muted-foreground">Web Push wymaga internetu, zgody przeglądarki i opcjonalnego pakietu <code>python3-pywebpush</code> w Ubuntu. Przekazuje wyłącznie ogólny typ zdarzenia, bez tematu poczty, adresów i haseł. Otwarte karty (także zminimalizowane) wznawiają obecność; po awarii lub uśpieniu przeglądarki jej wykrycie może być opóźnione.</p>
        <div className="divide-y rounded-md border px-3">{notificationCategories.map(category=><div key={category.id} className="flex items-center justify-between gap-2 py-2"><span className="text-sm">{category.label}</span><Switch aria-label={'Push: '+category.label} checked={model.state.push_categories.includes(category.id)} disabled={model.working} onCheckedChange={value=>void model.changeCategory(category.id,value)}/></div>)}</div>
        <p className="text-xs text-muted-foreground">Historia: maksymalnie 200 najnowszych wpisów, do 30 dni. Możesz usuwać pojedyncze wpisy i wyczyścić wszystko w dowolnym momencie.</p>
      </CardContent></Card>
  </div>
}
