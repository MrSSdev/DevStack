import { useEffect, useRef, useState } from 'react'
import { Activity, ChevronRight, Code2, Database, Globe2, HardDrive, Mail, Play, RefreshCw, Square, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { runOperation } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export type ManagedService={id:string;unit:string;name:string;description:string;state:string;load_state:string;widget_selected:boolean;panel_dependency:boolean;autostart:boolean|null;autostart_editable:boolean}
type Props={services:ManagedService[];csrf:string;refresh:()=>Promise<void>}
type Action='start'|'stop'|'restart'
const icons={nginx:Globe2,php:Code2,mariadb:Database,redis:HardDrive,mailpit:Mail}
const labels:Record<string,string>={active:'Działa',inactive:'Zatrzymana',failed:'Błąd',activating:'Uruchamianie',deactivating:'Zatrzymywanie',reloading:'Przeładowanie',unknown:'Brak danych'}
const actionLabels:Record<Action,string>={start:'Uruchom',stop:'Zatrzymaj',restart:'Uruchom ponownie'}
function Status({state}:{state:string}){return <Badge className={state==='active'?'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300':undefined}>{labels[state]??state}</Badge>}

export function ManagedServices({services,csrf,refresh,blocked,statusUnavailable,onAutostartSaved}:Props&{blocked:boolean;statusUnavailable:boolean;onAutostartSaved:(id:string,enabled:boolean)=>void}){
  const [selected,setSelected]=useState('')
  const [confirm,setConfirm]=useState<Action|null>(null)
  const [busy,setBusy]=useState(false)
  const dialog=useRef<HTMLDialogElement>(null)
  const current=services.find(s=>s.id===selected)
  useEffect(()=>{if(current&&!dialog.current?.open)dialog.current?.showModal();if(!current&&dialog.current?.open)dialog.current.close()},[current])
  function close(){if(busy)return;setSelected('');setConfirm(null);dialog.current?.close()}
  async function execute(){
    if(!current||!confirm||busy||blocked||statusUnavailable)return
    setBusy(true)
    try{
      const result=await runOperation('/api/services/control',csrf,{service:current.id,action:confirm,confirmed:true},{
        title:'Zlecanie operacji…',description:current.name+' — '+actionLabels[confirm],
        onResult:()=>({kind:'info',title:'Polecenie przyjęte: '+current.name+' — '+actionLabels[confirm]+'.',description:'Wynik operacji pojawi się w powiadomieniach.'}),
        formatError:e=>(e instanceof Error?e.message:'Brak potwierdzenia.')+' Sprawdź stan przed ponowieniem.'
      })
      if(result!==undefined){
        setConfirm(null)
        // Do not retry a command. Only refresh status after the one-shot has begun.
        await refresh()
        window.setTimeout(()=>{void refresh()},3500)
      }
    }finally{setBusy(false)}
  }
  async function saveAutostart(enabled:boolean){
    if(!current||busy||blocked||statusUnavailable||confirm||!current.autostart_editable||current.load_state!=='loaded')return
    const id=current.id
    setBusy(true)
    try{
      await runOperation<{service:string;autostart:boolean}>('/api/services/autostart',csrf,{service:id,enabled},{
        title:'Zapisywanie autostartu…',description:current.name,errorFallback:'Nie udało się zapisać autostartu.',
        onResult:result=>{
          if(result.service!==id||result.autostart!==enabled)throw new Error('Nie udało się potwierdzić ustawienia autostartu.')
          onAutostartSaved(id,result.autostart)
          return enabled?'Włączono autostart usługi.':'Wyłączono autostart usługi.'
        }
      })
    }finally{await refresh();setBusy(false)}
  }
  const unavailable=blocked||statusUnavailable||busy||current?.load_state!=='loaded'||['activating','deactivating','reloading'].includes(current?.state??'')
  return <Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle>Stan usług</CardTitle></div><Badge>systemd</Badge></div></CardHeader><CardContent className="space-y-3">
    {statusUnavailable&&<p role="status" className="text-xs text-muted-foreground">Nie można odczytać aktualnego stanu usług. Ponawianie odczytu…</p>}
    {blocked&&<p className="text-xs text-muted-foreground">Trwa operacja instalatora lub usługi. Sterowanie chwilowo niedostępne.</p>}
    {!services.length&&<p className="text-sm text-muted-foreground">Brak odczytu usług. Odśwież dane.</p>}
    <div className="divide-y">{services.map(s=>{const Icon=icons[s.id as keyof typeof icons]??Activity;return <button type="button" key={s.id} data-service-id={s.id} onClick={()=>{setSelected(s.id);setConfirm(null)}} className="flex w-full items-center gap-2 rounded-md py-2 text-left hover:bg-secondary/50"><span className="flex size-8 shrink-0 items-center justify-center rounded-xl border bg-secondary"><Icon className="size-4"/></span><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-1.5 text-sm font-medium"><span>{s.name}</span>{s.autostart===true&&<Badge title={s.panel_dependency?'Uruchamiana z DevStack':'Uruchamiana przy starcie Ubuntu'}>Autostart</Badge>}</span><span className="block text-xs text-muted-foreground">{s.description}</span></span><Status state={s.state}/><ChevronRight className="size-4 shrink-0 text-muted-foreground"/></button>})}</div>
    <dialog ref={dialog} onCancel={e=>{e.preventDefault();close()}} onClose={()=>{setSelected('');setConfirm(null)}} className="fixed inset-0 m-auto w-[min(94vw,28rem)] rounded-xl border bg-background p-4 text-foreground shadow-xl backdrop:bg-black/50">
      {current&&<div className="space-y-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{current.name}</h2><p className="text-sm text-muted-foreground">{current.description}</p></div><Button variant="ghost" size="icon" aria-label="Zamknij" disabled={busy} onClick={close}><X/></Button></div><div className="flex items-center justify-between gap-2"><code className="break-all text-xs">{current.unit}</code><Status state={current.state}/></div>
        {current.panel_dependency&&<p className="rounded-md border p-3 text-xs leading-relaxed text-muted-foreground">Zatrzymanie tej usługi wyłączy również panel DevStack. Przywróć ją w widżecie Windows przez restart (jeśli jest tam widoczna) albo w Ubuntu: <code>sudo systemctl start {current.unit}</code>. Restart może chwilowo przerwać dostęp.</p>}
        <div className="flex items-center justify-between gap-3 rounded-md border p-3"><div className="min-w-0"><p className="text-sm font-medium">Autostart</p><p className="text-xs text-muted-foreground">{current.panel_dependency?'Wymagana przez DevStack — uruchamiana z panelem.':current.autostart_editable?'Uruchamiaj przy starcie Ubuntu (WSL). Nie zmienia bieżącego stanu.':'Ustawienie niedostępne dla tej usługi.'}</p></div>{current.autostart===null?<Badge>Niedostępny</Badge>:<Switch aria-label="Autostart" checked={current.autostart===true} disabled={unavailable||!current.autostart_editable||!!confirm} onCheckedChange={enabled=>{void saveAutostart(enabled)}}/>}</div>
        {confirm?<div className="space-y-3 border-t pt-3"><p className="text-sm">{actionLabels[confirm]}: {current.name}? {confirm!=='start'?'Bieżące połączenia mogą zostać przerwane.':''}</p><div className="flex gap-2"><Button variant={confirm==='stop'?'destructive':'default'} disabled={unavailable} onClick={()=>{void execute()}}>{busy?'Zlecanie…':'Potwierdź'}</Button><Button variant="outline" disabled={busy} onClick={()=>setConfirm(null)}>Anuluj</Button></div></div>:<div className="grid gap-2"><Button variant="outline" disabled={unavailable||current.state==='active'} onClick={()=>setConfirm('start')}><Play/>Uruchom</Button><Button variant="outline" disabled={unavailable} onClick={()=>setConfirm('restart')}><RefreshCw/>Uruchom ponownie</Button><Button variant="outline" disabled={unavailable||current.state==='inactive'} onClick={()=>setConfirm('stop')}><Square/>Zatrzymaj</Button></div>}
      </div>}
    </dialog>
  </CardContent></Card>
}

export function WidgetServiceSettings({services,csrf,refresh}:Props){
  const saved=services.filter(s=>s.widget_selected).map(s=>s.id)
  const [draft,setDraft]=useState<string[]|null>(null)
  const selected=draft??saved
  const [busy,setBusy]=useState(false)
  async function save(){
    if(busy||!draft)return
    setBusy(true)
    try{
      await runOperation<{services:string[]}>('/api/widget/services',csrf,{services:draft.filter(id=>services.some(s=>s.id===id))},{
        title:'Zapisywanie wyboru usług…',errorFallback:'Nie udało się zapisać.',
        onResult:async result=>{
          setDraft(result.services);await refresh()
          setDraft(null)
          return 'Zapisano wybór usług.'
        }
      })
    }finally{setBusy(false)}
  }
  return <Card><CardHeader><CardTitle>Widżet DevStack w Windows</CardTitle><CardDescription>Usługi widoczne w widżecie Windows.</CardDescription></CardHeader><CardContent className="space-y-3">
    {!services.length&&<p className="text-sm text-muted-foreground">Brak odczytu usług.</p>}
    {services.map(s=><label className="flex items-center gap-3 text-sm" key={s.id}><input type="checkbox" className="size-4 accent-primary" disabled={busy} checked={selected.includes(s.id)} onChange={e=>{setDraft(e.target.checked?[...selected,s.id]:selected.filter(id=>id!==s.id))}}/><span className="flex-1">{s.name}</span><Status state={s.state}/></label>)}
    <div className="flex gap-2 pt-2"><Button disabled={busy||draft===null||!services.length} onClick={()=>{void save()}}>{busy?'Zapisywanie…':'Zapisz wybór'}</Button><Button variant="outline" disabled={busy||draft===null} onClick={()=>{setDraft(null)}}>Anuluj</Button></div>
  </CardContent></Card>
}
