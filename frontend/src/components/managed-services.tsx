import { useEffect, useRef, useState } from 'react'
import { Activity, ChevronRight, Code2, Database, Globe2, HardDrive, Mail, Play, RefreshCw, Square, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export type ManagedService={id:string;unit:string;name:string;description:string;state:string;load_state:string;widget_selected:boolean;panel_dependency:boolean}
type Request=<T>(method:'GET'|'POST',path:string,csrf?:string,data?:unknown)=>Promise<T>
type Props={services:ManagedService[];csrf:string;request:Request;refresh:()=>Promise<void>}
type Action='start'|'stop'|'restart'
const icons={nginx:Globe2,php:Code2,mariadb:Database,redis:HardDrive,mailpit:Mail}
const labels:Record<string,string>={active:'Działa',inactive:'Zatrzymana',failed:'Błąd',activating:'Uruchamianie',deactivating:'Zatrzymywanie',reloading:'Przeładowanie',unknown:'Brak danych'}
const actionLabels:Record<Action,string>={start:'Uruchom',stop:'Zatrzymaj',restart:'Uruchom ponownie'}
function Status({state}:{state:string}){return <Badge className={state==='active'?'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300':undefined}>{labels[state]??state}</Badge>}

export function ManagedServices({services,csrf,request,refresh,blocked}:Props&{blocked:boolean}){
  const [selected,setSelected]=useState('')
  const [confirm,setConfirm]=useState<Action|null>(null)
  const [busy,setBusy]=useState(false)
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const dialog=useRef<HTMLDialogElement>(null)
  const current=services.find(s=>s.id===selected)
  useEffect(()=>{if(current&&!dialog.current?.open)dialog.current?.showModal();if(!current&&dialog.current?.open)dialog.current.close()},[current])
  function close(){if(busy)return;setSelected('');setConfirm(null);dialog.current?.close()}
  async function execute(){
    if(!current||!confirm||busy||blocked)return
    setBusy(true);setError('');setNotice('')
    try{
      await request('POST','/api/services/control',csrf,{service:current.id,action:confirm,confirmed:true})
      setNotice('Polecenie przyjęte: '+current.name+' — '+actionLabels[confirm]+'. Wynik sprawdź po odświeżeniu stanu.')
      setConfirm(null)
      // Do not retry a command. Only refresh status after the one-shot has begun.
      await refresh()
      window.setTimeout(()=>{void refresh()},3500)
    }catch(e){setError((e instanceof Error?e.message:'Brak potwierdzenia.')+' Sprawdź stan przed ponowieniem.')}
    finally{setBusy(false)}
  }
  const unavailable=blocked||busy||current?.load_state!=='loaded'||['activating','deactivating','reloading'].includes(current?.state??'')
  return <Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle>Stan usług</CardTitle></div><Badge>systemd</Badge></div></CardHeader><CardContent className="space-y-3">
    {notice&&<p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
    {blocked&&<p className="text-xs text-muted-foreground">Trwa operacja instalatora lub usługi. Sterowanie chwilowo niedostępne.</p>}
    {!services.length&&<p className="text-sm text-muted-foreground">Brak odczytu usług. Odśwież dane.</p>}
    <div className="divide-y">{services.map(s=>{const Icon=icons[s.id as keyof typeof icons]??Activity;return <button type="button" key={s.id} onClick={()=>{setSelected(s.id);setConfirm(null);setError('')}} className="flex w-full items-center gap-2 rounded-md py-2 text-left hover:bg-secondary/50"><span className="flex size-8 shrink-0 items-center justify-center rounded-xl border bg-secondary"><Icon className="size-4"/></span><span className="min-w-0 flex-1"><span className="block text-sm font-medium">{s.name}</span><span className="block text-xs text-muted-foreground">{s.description}</span></span><Status state={s.state}/><ChevronRight className="size-4 shrink-0 text-muted-foreground"/></button>})}</div>
    <dialog ref={dialog} onCancel={e=>{e.preventDefault();close()}} onClose={()=>{setSelected('');setConfirm(null)}} className="fixed inset-0 m-auto w-[min(94vw,28rem)] rounded-xl border bg-background p-4 text-foreground shadow-xl backdrop:bg-black/50">
      {current&&<div className="space-y-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{current.name}</h2><p className="text-sm text-muted-foreground">{current.description}</p></div><Button variant="ghost" size="icon" aria-label="Zamknij" disabled={busy} onClick={close}><X/></Button></div><div className="flex items-center justify-between gap-2"><code className="break-all text-xs">{current.unit}</code><Status state={current.state}/></div>
        {current.panel_dependency&&<p className="rounded-md border p-3 text-xs leading-relaxed text-muted-foreground">Zatrzymanie tej usługi wyłączy również panel DevStack. Przywróć ją w widżecie Windows przez restart (jeśli jest tam widoczna) albo w Ubuntu: <code>sudo systemctl start {current.unit}</code>. Restart może chwilowo przerwać dostęp.</p>}
        {confirm?<div className="space-y-3 border-t pt-3"><p className="text-sm">{actionLabels[confirm]}: {current.name}? {confirm!=='start'?'Bieżące połączenia mogą zostać przerwane.':''}</p><div className="flex gap-2"><Button variant={confirm==='stop'?'destructive':'default'} disabled={unavailable} onClick={()=>{void execute()}}>{busy?'Zlecanie…':'Potwierdź'}</Button><Button variant="outline" disabled={busy} onClick={()=>setConfirm(null)}>Anuluj</Button></div></div>:<div className="grid gap-2"><Button variant="outline" disabled={unavailable||current.state==='active'} onClick={()=>setConfirm('start')}><Play/>Uruchom</Button><Button variant="outline" disabled={unavailable} onClick={()=>setConfirm('restart')}><RefreshCw/>Uruchom ponownie</Button><Button variant="outline" disabled={unavailable||current.state==='inactive'} onClick={()=>setConfirm('stop')}><Square/>Zatrzymaj</Button></div>}
        {notice&&<p role="status" className="text-xs text-muted-foreground">{notice}</p>}{error&&<p role="alert" className="text-xs text-destructive">{error}</p>}
      </div>}
    </dialog>
  </CardContent></Card>
}

export function WidgetServiceSettings({services,csrf,request,refresh}:Props){
  const saved=services.filter(s=>s.widget_selected).map(s=>s.id)
  const [draft,setDraft]=useState<string[]|null>(null)
  const selected=draft??saved
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  async function save(){
    if(busy||!draft)return
    setBusy(true);setError('');setMessage('')
    try{
      const result=await request<{services:string[]}>('POST','/api/widget/services',csrf,{services:draft.filter(id=>services.some(s=>s.id===id))})
      setDraft(result.services);await refresh()
      setDraft(null);setMessage('Zapisano wybór usług.')
    }catch(e){setError(e instanceof Error?e.message:'Nie udało się zapisać.')}
    finally{setBusy(false)}
  }
  return <Card><CardHeader><CardTitle>Widżet DevStack w Windows</CardTitle><CardDescription>Usługi widoczne w widżecie Windows.</CardDescription></CardHeader><CardContent className="space-y-3">
    {!services.length&&<p className="text-sm text-muted-foreground">Brak odczytu usług.</p>}
    {services.map(s=><label className="flex items-center gap-3 text-sm" key={s.id}><input type="checkbox" className="size-4 accent-primary" disabled={busy} checked={selected.includes(s.id)} onChange={e=>{setDraft(e.target.checked?[...selected,s.id]:selected.filter(id=>id!==s.id));setMessage('')}}/><span className="flex-1">{s.name}</span><Status state={s.state}/></label>)}
    <div className="flex gap-2 pt-2"><Button disabled={busy||draft===null||!services.length} onClick={()=>{void save()}}>{busy?'Zapisywanie…':'Zapisz wybór'}</Button><Button variant="outline" disabled={busy||draft===null} onClick={()=>{setDraft(null);setMessage('')}}>Anuluj</Button></div>
    {message&&<p role="status" className="text-xs text-muted-foreground">{message}</p>}{error&&<p role="alert" className="text-xs text-destructive">{error}</p>}
  </CardContent></Card>
}
