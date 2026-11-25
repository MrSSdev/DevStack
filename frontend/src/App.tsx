import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  Activity, ArrowLeft, ArrowRight, Blocks, Check, ChevronRight, CircleAlert, Code2,
  Copy, Database, ExternalLink, FileCode2, FolderCode, Globe2, HardDrive, Pencil, ChevronDown, ChevronUp,
  LayoutDashboard, LockKeyhole, LogOut, Menu, Moon, Plus, RefreshCw,
  Settings2, ShieldCheck, Sun, Trash2, X,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

type View = 'dashboard' | 'projects' | 'new' | 'project' | 'settings'

// The native Windows Widgets link to /#/projects and /#/new. Keep this
// routing entirely client-side: no new server endpoints or auth bypass.
function viewFromHash(): View {
  switch (window.location.hash) {
    case '#/projects': return 'projects'
    case '#/new': return 'new'
    case '#/settings': return 'settings'
    default: return 'dashboard'
  }
}
type Project = {
  slug: string; name: string; domain: string; path: string; webroot: string
  database: boolean; https: boolean; db_name: string; db_user: string
  nginx_config: string; overrides: string; extra_locations?: string
  engine?: string; starter_kit?: string; starter_kit_label?: string; engine_status?: string
}
type StarterKit = { id:string; label:string }
type ToolInfo = { composer: boolean; installed: string|null; latest?: string|null; starter_kits?:StarterKit[]|null }
type EngineDefinition = { label:string; tool:string; webroot:string; default_option:string; option_label:string; option_hint:string; description:string; database_options?:{id:string;label:string}[]; database_hint?:string }
type EngineJob = { id: string; engine?: string; action: string; state: string; message: string; slug?: string; log?: string; tool?: ToolInfo }
type State = { services: Record<string,string>; projects: Project[]; preferences: { theme: 'light'|'dark' }; engine_job?: EngineJob|null; engines?: Record<string,EngineDefinition>; engine_access_allowed?: boolean }
type Envelope<T> = { ok: boolean; result?: T; error?: string; authenticated?: boolean; csrf?: string }
type Draft = { name: string; slug: string; domain: string; webroot: string; database: boolean; https: boolean; engine: string; starter_kit: string }

const blank: Draft = { name: '', slug: '', domain: '', webroot: 'public', database: true, https: true, engine: 'none', starter_kit: 'none' }
const serviceInfo = [
  { key: 'nginx', name: 'Nginx', detail: 'Serwer WWW i HTTPS', icon: Globe2 },
  { key: 'php8.5-fpm', name: 'PHP-FPM 8.5', detail: 'Interpreter aplikacji', icon: Code2 },
  { key: 'mariadb', name: 'MariaDB', detail: 'Relacyjna baza danych', icon: Database },
  { key: 'redis-server', name: 'Redis', detail: 'Cache i kolejki', icon: HardDrive },
]

async function request<T>(method: 'GET'|'POST', path: string, csrf?: string, data?: unknown): Promise<T> {
  const response = await fetch(path, {
    method, credentials: 'same-origin', cache: 'no-store',
    headers: { ...(data ? {'Content-Type':'application/json'} : {}), ...(method === 'POST' ? {'X-CSRF-Token':csrf ?? ''} : {}) },
    ...(data ? {body:JSON.stringify(data)} : {}),
  })
  let body: Envelope<T>
  try { body = await response.json() as Envelope<T> }
  catch { throw new Error(`Nieprawidłowa odpowiedź serwera (HTTP ${response.status}).`) }
  if (!response.ok || !body.ok) throw new Error(body.error || `Błąd HTTP ${response.status}.`)
  return (body.result ?? body) as T
}

function Dot({ state }: {state:string}) {
  const good=state==='active'
  return <Badge className={good?'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300':'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-950 dark:bg-amber-950 dark:text-amber-300'}><span className={cn('size-1.5 rounded-full',good?'bg-emerald-500':'bg-amber-500')}/>{good?'Działa':state||'Nieznany'}</Badge>
}
function Field({label,hint,children}:{label:string;hint?:string;children:ReactNode}) {
  return <div className="space-y-1.5"><label className="block text-sm font-medium">{label}</label>{children}{hint&&<p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}</div>
}
function IconBox({children}:{children:ReactNode}) {
  return <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border bg-secondary text-secondary-foreground">{children}</span>
}
function Metric({label,value,icon,foot}:{label:string;value:string;icon:ReactNode;foot:string}) {
  return <Card><CardContent className="p-5"><div className="flex items-start justify-between gap-3"><span className="text-sm font-medium text-muted-foreground">{label}</span><span className="text-muted-foreground">{icon}</span></div><div className="mt-4 text-3xl font-semibold tracking-tight">{value}</div><p className="mt-2 text-xs text-muted-foreground">{foot}</p></CardContent></Card>
}
function Info({label,value,action}:{label:string;value:string;action?:ReactNode}) {
  return <div className="flex min-h-8 items-center gap-3"><span className="w-26 shrink-0 text-xs text-muted-foreground">{label}</span><span className="min-w-0 flex-1 break-all text-sm font-medium">{value}</span>{action}</div>
}

export function App() {
  const [csrf,setCsrf]=useState('')
  const [authenticated,setAuthenticated]=useState(false)
  const [passwordRequired,setPasswordRequired]=useState(true)
  const [initializing,setInitializing]=useState(true)
  const [loginPassword,setLoginPassword]=useState('')
  // The server sets the theme class in <html> before CSS/JS load.
  // Hydrate React from that exact value to avoid a light-mode flash.
  const [theme,setTheme]=useState<'light'|'dark'>(()=>
    document.documentElement.classList.contains('dark')?'dark':'light'
  )
  const [savingTheme,setSavingTheme]=useState(false)
  const [refreshing,setRefreshing]=useState(false)
  const [lastRefreshed,setLastRefreshed]=useState<Date|null>(null)
  const themeSaveRef=useRef(false)
  const refreshRef=useRef(false)
  const [navOpen,setNavOpen]=useState(false)
  const [view,setView]=useState<View>(viewFromHash)
  const [server,setServer]=useState<State>({services:{},projects:[],preferences:{theme:'light'}})
  const [selected,setSelected]=useState('')
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [draft,setDraft]=useState<Draft>(blank)
  const [selectedTool,setSelectedTool]=useState('')
  const [dismissedJob,setDismissedJob]=useState('')
  const [toolInfo,setToolInfo]=useState<ToolInfo|null>(null)
  const [toolLoading,setToolLoading]=useState(false)
  const [toolError,setToolError]=useState('')
  const [kitInfo,setKitInfo]=useState<{engine:string; info:ToolInfo}|null>(null)
  const [kitLoading,setKitLoading]=useState(false)
  const [kitError,setKitError]=useState('')
  const [kitRefresh,setKitRefresh]=useState(0)
  const [overrides,setOverrides]=useState('')
  const [extraLocations,setExtraLocations]=useState('')
  const [fullOpen,setFullOpen]=useState(false)
  const [fullEditing,setFullEditing]=useState(false)
  const [fullConfig,setFullConfig]=useState('')
  const [fullPassword,setFullPassword]=useState('')
  const [revealPassword,setRevealPassword]=useState('')
  const [dbSecret,setDbSecret]=useState('')
  const [deleteOpen,setDeleteOpen]=useState(false)
  const [deleteConfirmation,setDeleteConfirmation]=useState('')
  const [deleteDbConfirm,setDeleteDbConfirm]=useState(false)
  const [testingDb,setTestingDb]=useState(false)
  const [caFingerprint,setCaFingerprint]=useState('')
  const [caInfoError,setCaInfoError]=useState(false)

  const projects=server.projects
  const engineJob=server.engine_job
  const engines=server.engines??{}
  const engineAllowed=server.engine_access_allowed===true
  const draftEngine=engines[draft.engine]
  const installedKitInfo=kitInfo?.engine===draft.engine?kitInfo.info:null
  const starterKits=installedKitInfo?.starter_kits??[]
  const kitReady=!kitLoading&&starterKits.some(o=>o.id===draft.starter_kit)
  const toolId=engines[selectedTool]?selectedTool:Object.keys(engines)[0]??''
  const toolDefinition=engines[toolId]
  const engineBusy=!!engineJob && !['succeeded','failed'].includes(engineJob.state)
  const current=projects.find(p=>p.slug===selected)
  const title:Record<View,string>={dashboard:'Przegląd',projects:'Projekty',new:'Nowy projekt',project:current?.name??'Projekt',settings:'Ustawienia'}
  const notify=(message:string)=>{setNotice(message);setError('')}
  const fail=(reason:unknown)=>{setError(reason instanceof Error?reason.message:'Nieznany błąd.');setNotice('')}

  const refresh=useCallback(async(manual=false)=>{
    if(refreshRef.current)return
    refreshRef.current=true
    if(manual)setRefreshing(true)
    try {
      const next=await request<State>('GET','/api/state')
      setServer(next)
      if(!themeSaveRef.current && (next.preferences?.theme==='dark'||next.preferences?.theme==='light'))setTheme(next.preferences.theme)
      setLastRefreshed(new Date())
      if(manual){setNotice('Dane usług i projektów zostały odświeżone.');setError('')}
    }catch(e){
      setError(e instanceof Error?e.message:'Nie udało się odświeżyć danych.')
      setNotice('')
    }finally{
      refreshRef.current=false
      if(manual)setRefreshing(false)
    }
  },[])
  async function toggleTheme(){
    if(savingTheme || themeSaveRef.current)return
    const next=theme==='light'?'dark':'light'
    themeSaveRef.current=true
    setSavingTheme(true)
    try{
      const saved=await request<{theme:'light'|'dark'}>('POST','/api/preferences',csrf,{theme:next})
      setTheme(saved.theme)
      setServer(prev=>({...prev,preferences:{theme:saved.theme}}))
      setNotice(saved.theme==='dark'?'Zapisano ciemny motyw na serwerze.':'Zapisano jasny motyw na serwerze.')
      setError('')
    }catch(e){
      setError(e instanceof Error?e.message:'Nie udało się zapisać motywu.')
      setNotice('')
    }finally{
      themeSaveRef.current=false
      setSavingTheme(false)
    }
  }
  useEffect(()=>{
    let disposed=false
    void (async()=>{
      try {
        const s=await request<{csrf:string;authenticated:boolean;password_required:boolean}>('GET','/api/session')
        if (!disposed) {setCsrf(s.csrf);setAuthenticated(s.authenticated);setPasswordRequired(s.password_required)}
        if (!disposed&&s.authenticated) await refresh()
      } catch(e) {if(!disposed)fail(e)}
      finally {if(!disposed)setInitializing(false)}
    })()
    return ()=>{disposed=true}
  },[refresh])
  useEffect(()=>{document.documentElement.classList.toggle('dark',theme==='dark')},[theme])
  useEffect(()=>{
    // Support direct bookmarks from Windows Widgets, plus browser Back/Forward.
    const syncRoute=()=>{setView(viewFromHash());setNavOpen(false);setDbSecret('')}
    window.addEventListener('hashchange',syncRoute)
    return ()=>window.removeEventListener('hashchange',syncRoute)
  },[])
  useEffect(()=>{
    if(!authenticated || view!=='settings')return
    let disposed=false
    setCaFingerprint('')
    setCaInfoError(false)
    void request<{sha256:string}>('GET','/api/ca/info')
      .then(info=>{if(!disposed)setCaFingerprint(info.sha256)})
      .catch(()=>{if(!disposed)setCaInfoError(true)})
    return ()=>{disposed=true}
  },[authenticated,view])
  useEffect(()=>{
    if(!authenticated)return
    const id=window.setInterval(()=>{void refresh()},30000)
    return ()=>window.clearInterval(id)
  },[authenticated,refresh])
  useEffect(()=>{
    setOverrides(current?.overrides??'')
    setExtraLocations(current?.extra_locations??'')
    setFullOpen(false)
    setFullEditing(false)
    setFullPassword('')
    setDbSecret('')
    setRevealPassword('')
    setDeleteOpen(false)
    setDeleteConfirmation('')
    setDeleteDbConfirm(false)
  },[selected])
  useEffect(()=>{if(!fullEditing)setFullConfig(current?.nginx_config??'')},[current?.nginx_config,fullEditing])
  useEffect(()=>{if(!dbSecret)return;const timer=window.setTimeout(()=>setDbSecret(''),20000);return ()=>window.clearTimeout(timer)},[dbSecret])

  async function login(e:FormEvent<HTMLFormElement>){
    e.preventDefault();setBusy(true);setError('')
    try {
      const info=await request<{authenticated:boolean;csrf:string}>('POST','/api/login',csrf,{password:loginPassword})
      setCsrf(info.csrf);setAuthenticated(true);setLoginPassword('');await refresh()
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function logout(){
    try {const info=await request<{csrf:string}>('POST','/api/logout',csrf,{});setCsrf(info.csrf)}
    catch(e){fail(e);return}
    setAuthenticated(false);setDbSecret('');setServer({services:{},projects:[],preferences:{theme:'light'}});setSelected('');setView('dashboard')
  }
  useEffect(()=>{
    if(!authenticated || !engineBusy)return
    const id=window.setInterval(()=>{if(document.visibilityState==='visible')void refresh()},1500)
    return ()=>window.clearInterval(id)
  },[authenticated,engineBusy,refresh])
  useEffect(()=>{
    if(!authenticated || !engineAllowed || view!=='settings' || engineBusy || !toolId)return
    let disposed=false
    setToolLoading(true);setToolError('');setToolInfo(null)
    void request<ToolInfo>('GET','/api/engines/'+encodeURIComponent(toolId))
      .then(info=>{if(!disposed){setToolInfo(info);setToolError('')}})
      .catch(e=>{if(!disposed)setToolError(e instanceof Error?e.message:'Błąd odczytu instalatora.')})
      .finally(()=>{if(!disposed)setToolLoading(false)})
    return ()=>{disposed=true}
  },[authenticated,engineAllowed,view,engineBusy,engineJob?.id,toolId])
  useEffect(()=>{
    if(!authenticated || !engineAllowed || view!=='new' || engineBusy || draft.engine==='none'){
      setKitInfo(null);setKitLoading(false);setKitError('')
      return
    }
    const id=draft.engine
    let disposed=false
    setKitInfo(null);setKitLoading(true);setKitError('')
    void request<ToolInfo>('GET','/api/engines/'+encodeURIComponent(id))
      .then(info=>{
        if(disposed)return
        setKitInfo({engine:id,info})
        if(info.installed && !info.starter_kits?.length)setKitError('Nie udało się odczytać starter kitów z aktualnego instalatora.')
        else if(!info.installed)setKitError('Najpierw zainstaluj oficjalny instalator w ustawieniach.')
        setDraft(prev=>prev.engine===id && info.starter_kits?.length && !info.starter_kits.some(o=>o.id===prev.starter_kit)
          ? {...prev,starter_kit:''}:prev)
      })
      .catch(e=>{if(!disposed)setKitError(e instanceof Error?e.message:'Nie udało się odczytać starter kitów.')})
      .finally(()=>{if(!disposed)setKitLoading(false)})
    return ()=>{disposed=true}
  },[authenticated,engineAllowed,view,engineBusy,engineJob?.id,draft.engine,kitRefresh])
  async function toolAction(action:'check'|'install-tool'|'update-tool'){
    if(busy || engineBusy || toolLoading || !toolId)return
    setBusy(true);setError('')
    try{
      await request<{message:string;job_id:string}>('POST','/api/engines/'+encodeURIComponent(toolId),csrf,{action})
      await refresh()
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function create(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    if(busy||engineBusy||(!!draftEngine&&(!engineAllowed||!kitReady)))return
    setBusy(true);setError('')
    try {
      const payload={...draft,slug:draft.slug.trim(),domain:(draft.domain.trim()||`${draft.slug.trim()}.test`).toLowerCase(),webroot:draft.webroot.trim()}
      const answer=await request<{message:string;job_id?:string}>('POST','/api/projects',csrf,payload)
      await refresh();setDraft(blank)
      if(!answer.job_id){setSelected(payload.slug);setView('project');notify(answer.message)}
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function testDatabase(){
    if(!current || !current.database || busy || testingDb)return
    setTestingDb(true);setError('');setNotice('')
    try{
      const result=await request<{message:string}>('POST','/api/projects/test-db',csrf,{slug:current.slug})
      notify(result.message)
    }catch(e){fail(e)}finally{setTestingDb(false)}
  }
  async function deleteProject(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    if(!current || deleteConfirmation!==current.slug || (current.database&&!deleteDbConfirm))return
    setBusy(true);setError('')
    try{
      const result=await request<{message:string}>('POST','/api/projects/delete',csrf,{
        slug:current.slug,confirmation:deleteConfirmation,delete_database:current.database && deleteDbConfirm
      })
      setDeleteOpen(false)
      setDeleteConfirmation('')
      setDeleteDbConfirm(false)
      setSelected('')
      setView('projects')
      setDbSecret('')
      await refresh()
      notify(result.message)
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function saveConfig(){
    if(!current)return;setBusy(true)
    try {
      const result=await request<{message:string}>('POST','/api/config',csrf,{slug:current.slug,overrides,extra_locations:extraLocations})
      await refresh();notify(result.message)
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function saveFullConfig(){
    if(!current || (passwordRequired && !fullPassword))return
    setBusy(true)
    try {
      const result=await request<{message:string;overrides:string;extra_locations:string}>('POST','/api/config/full',csrf,{slug:current.slug,full_config:fullConfig,password:fullPassword})
      setOverrides(result.overrides)
      setExtraLocations(result.extra_locations)
      setFullPassword('')
      setFullEditing(false)
      await refresh()
      notify(result.message)
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  function toggleFullEdit(){
    if(!current)return
    if(fullEditing){setFullEditing(false);setFullConfig(current.nginx_config);setFullPassword('');return}
    if(overrides!==current.overrides || extraLocations!==(current.extra_locations??'')){fail(new Error('Najpierw zapisz lub cofnij niezapisane zmiany reguł Nginx.'));return}
    setFullConfig(current.nginx_config)
    setFullPassword('')
    setFullEditing(true)
  }
  async function reveal(e:FormEvent<HTMLFormElement>){
    e.preventDefault();if(!current)return;setBusy(true);setError('')
    try {
      const result=await request<{password:string}>('POST','/api/reveal',csrf,{slug:current.slug,password:revealPassword})
      setDbSecret(result.password);setRevealPassword('');notify('Hasło wyświetlone na 20 sekund. Nie zapisujemy go w przeglądarce.')
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function copy(value:string){
    try {await navigator.clipboard.writeText(value);notify('Skopiowano do schowka.')}
    catch {setError('Przeglądarka zablokowała schowek.')}
  }
  function go(next:View){
    setView(next);setNavOpen(false);setDbSecret('');setError('');setNotice('')
    // Keep a selected project's in-memory detail view as-is. All other
    // views have stable, shareable hash URLs used by Windows Widgets.
    if(next!=='project')window.location.hash='/'+next
  }
  function details(slug:string){setSelected(slug);go('project')}
  const linkStyle=(active:boolean)=>cn('flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',active?'bg-foreground text-background':'text-muted-foreground hover:bg-secondary hover:text-foreground')

  if(initializing) return <div className="flex min-h-screen items-center justify-center gap-3 text-sm text-muted-foreground"><RefreshCw className="size-5 animate-spin"/>Uruchamianie DevStack…</div>
  if(!authenticated) return <div className="flex min-h-screen items-center justify-center bg-background p-6"><div className="w-full max-w-sm space-y-6"><div className="flex justify-center"><span className="flex size-14 items-center justify-center rounded-2xl bg-foreground text-background"><Blocks className="size-7"/></span></div><div className="text-center"><h1 className="text-3xl font-semibold tracking-tight">DevStack</h1><p className="mt-2 text-sm text-muted-foreground">Twoje lokalne środowisko programistyczne.</p></div><Card><CardHeader><CardTitle>Logowanie</CardTitle><CardDescription>Wprowadź hasło panelu ustawione podczas instalacji.</CardDescription></CardHeader><CardContent><form onSubmit={login} className="space-y-4"><Field label="Hasło administratora"><Input type="password" autoComplete="current-password" minLength={1} value={loginPassword} onChange={e=>setLoginPassword(e.target.value)} required autoFocus/></Field><Button className="w-full" type="submit" disabled={busy}>{busy?'Sprawdzanie…':'Zaloguj się'}<ArrowRight/></Button></form></CardContent></Card>{error&&<p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}<p className="text-center text-xs text-muted-foreground"><LockKeyhole className="mr-1 inline size-3"/> Tylko lokalnie · HTTPS · WSL2</p></div></div>
  return <div className="min-h-screen bg-background">
    {navOpen&&<button type="button" aria-label="Zamknij menu" className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={()=>setNavOpen(false)}/>}
    <aside className={cn('fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r bg-card px-4 py-5 transition-transform lg:translate-x-0',navOpen?'translate-x-0':'-translate-x-full')}>
      <div className="flex items-center justify-between px-2 pb-8"><div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-xl bg-foreground text-background"><Blocks className="size-5"/></span><div><div className="font-semibold tracking-tight">DevStack</div><div className="text-[11px] text-muted-foreground">Local environment manager</div></div></div><Button size="icon" variant="ghost" className="lg:hidden" aria-label="Zamknij menu" onClick={()=>setNavOpen(false)}><X/></Button></div>
      <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Środowisko</p>
      <nav className="space-y-1" aria-label="Nawigacja główna"><button onClick={()=>go('dashboard')} className={linkStyle(view==='dashboard')}><LayoutDashboard className="size-4"/>Przegląd</button><button onClick={()=>go('projects')} className={linkStyle(view==='projects'||view==='project')}><FolderCode className="size-4"/>Projekty <span className="ml-auto text-xs opacity-70">{projects.length}</span></button><button onClick={()=>go('new')} className={linkStyle(view==='new')}><Plus className="size-4"/>Nowy projekt</button></nav>
      <p className="px-3 pb-2 pt-8 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">System</p><button onClick={()=>go('settings')} className={linkStyle(view==='settings')}><Settings2 className="size-4"/>Ustawienia</button>
      <div className="mt-auto space-y-3 rounded-xl border bg-secondary/50 p-3"><div className="flex items-center gap-2 text-xs font-medium"><ShieldCheck className="size-4 text-emerald-600"/> Lokalnie · WSL2</div><div className="break-all text-xs text-muted-foreground">https://devstack.test</div>{passwordRequired?<Button className="w-full" variant="outline" size="sm" onClick={()=>{void logout()}}><LogOut/>Wyloguj</Button>:<Badge>Tryb bez hasła</Badge>}</div>
    </aside>
    <div className="lg:pl-64"><header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur"><div className="mx-auto flex h-17 max-w-7xl items-center justify-between gap-3 px-5 sm:px-8"><div className="flex items-center gap-3"><Button size="icon" variant="ghost" className="lg:hidden" aria-label="Otwórz menu" onClick={()=>setNavOpen(true)}><Menu/></Button><span className="text-sm text-muted-foreground">DevStack</span><ChevronRight className="size-3 text-muted-foreground"/><span className="text-sm font-semibold">{title[view]}</span></div><div className="flex items-center gap-2"><Badge className="hidden sm:inline-flex">Ubuntu 26.04 · WSL2</Badge><span className="hidden text-xs text-muted-foreground md:inline" aria-live="polite">{lastRefreshed?'Aktualizacja: '+lastRefreshed.toLocaleTimeString('pl-PL',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'Oczekiwanie na dane'}</span><Button variant="outline" size="icon" aria-label={refreshing?'Odświeżanie danych':'Odśwież dane usług i projektów'} title={refreshing?'Odświeżanie…':'Odśwież dane usług i projektów'} disabled={refreshing} onClick={()=>{void refresh(true)}}><RefreshCw className={refreshing?'animate-spin':''}/></Button><Button variant="outline" size="icon" aria-label={theme==='light'?'Włącz ciemny motyw':'Włącz jasny motyw'} title={savingTheme?'Zapisywanie motywu…':'Zmień i zapisz motyw'} disabled={savingTheme} onClick={()=>{void toggleTheme()}}>{theme==='light'?<Moon/>:<Sun/>}</Button></div></div></header>
      <main className="mx-auto max-w-7xl space-y-7 px-5 py-8 sm:px-8 sm:py-10"><div className="flex flex-wrap items-end justify-between gap-4"><div><p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground">LOKALNE ŚRODOWISKO PROGRAMISTYCZNE</p><h1 className="text-3xl font-semibold tracking-tight">{title[view]}</h1><p className="mt-2 text-sm text-muted-foreground">Zarządzaj projektami i usługami z jednego miejsca.</p></div>{view!=='new'&&<Button onClick={()=>go('new')}><Plus/>Nowy projekt</Button>}</div>
      {notice&&<div role="status" className="flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"><Check className="size-4 shrink-0"/>{notice}<button className="ml-auto" aria-label="Zamknij" onClick={()=>setNotice('')}><X className="size-4"/></button></div>}
      {error&&<div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"><CircleAlert className="size-4 shrink-0"/>{error}<button className="ml-auto" aria-label="Zamknij" onClick={()=>setError('')}><X className="size-4"/></button></div>}
      {engineJob&&(engineBusy||engineJob.id!==dismissedJob)&&<Card><CardHeader><div className="flex items-center justify-between gap-2"><CardTitle>{engineJob.action==='create'?'Instalacja projektu':engines[engineJob.engine??'']?.tool??'Instalator'}</CardTitle>{!engineBusy&&<Button type="button" variant="ghost" size="icon" aria-label="Zamknij wynik instalatora" onClick={()=>setDismissedJob(engineJob.id)}><X/></Button>}</div><CardDescription>{engineJob.slug||'Narzędzia instalacyjne'}</CardDescription></CardHeader><CardContent className="space-y-3"><div className="flex flex-wrap items-center gap-2" role="status" aria-live="polite">{engineBusy?<RefreshCw className="size-4 animate-spin"/>:engineJob.state==='succeeded'?<Check className="size-4 text-emerald-600"/>:<CircleAlert className="size-4 text-destructive"/>}<span className="text-sm">{engineJob.message}</span></div>{engineJob.log&&<p className="text-xs text-muted-foreground">Prywatny log w Ubuntu: <code>~/.cache/devstack/engines/{engineJob.log}</code></p>}{!engineBusy&&engineJob.slug&&projects.some(p=>p.slug===engineJob.slug)&&<Button variant="outline" size="sm" onClick={()=>details(engineJob.slug!)}>Przejdź do projektu<ArrowRight/></Button>}{engineJob.state==='failed'&&<p className="text-xs text-muted-foreground">Nie ponawiamy instalacji automatycznie. Sprawdź stan projektu i log przed kolejną próbą.</p>}</CardContent></Card>}
      {view==='dashboard'&&<><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Projekty" value={String(projects.length)} foot="Skonfigurowane domeny .test" icon={<FolderCode className="size-5"/>}/><Metric label="Aktywne usługi" value={`${Object.values(server.services).filter(s=>s==='active').length}/4`} foot="Nginx, PHP, MariaDB, Redis" icon={<Activity className="size-5"/>}/><Metric label="HTTPS" value={String(projects.filter(p=>p.https).length)} foot="Projekty z Auto SSL" icon={<LockKeyhole className="size-5"/>}/><Metric label="Bazy danych" value={String(projects.filter(p=>p.database).length)} foot="Oddzielne konta MariaDB" icon={<Database className="size-5"/>}/></div>
        <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]"><Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle>Stan usług</CardTitle><CardDescription>Usługi uruchamiane przez systemd w Ubuntu</CardDescription></div><Badge>systemd</Badge></div></CardHeader><CardContent><div className="divide-y">{serviceInfo.map(s=><div key={s.key} className="flex items-center gap-3 py-3"><IconBox><s.icon className="size-4"/></IconBox><div className="min-w-0 flex-1"><div className="text-sm font-medium">{s.name}</div><div className="text-xs text-muted-foreground">{s.detail}</div></div><Dot state={server.services[s.key]??'unknown'}/></div>)}</div></CardContent></Card>
        <Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle>Ostatnie projekty</CardTitle><CardDescription>Przejdź do konfiguracji wybranej witryny</CardDescription></div><Button size="sm" variant="outline" onClick={()=>go('projects')}>Wszystkie<ArrowRight/></Button></div></CardHeader><CardContent>{projects.length===0?<p className="py-8 text-center text-sm text-muted-foreground">Brak projektów. Utwórz pierwszy projekt, aby rozpocząć.</p>:<div className="divide-y">{projects.slice(0,5).map(p=><button key={p.slug} className="flex w-full items-center gap-3 py-3 text-left hover:opacity-75" onClick={()=>details(p.slug)}><IconBox><FolderCode className="size-4"/></IconBox><div className="flex-1"><div className="text-sm font-medium">{p.name}</div><span className="text-xs text-muted-foreground">{p.domain}</span></div>{p.https&&<LockKeyhole className="size-4 text-emerald-600"/>}<ChevronRight className="size-4 text-muted-foreground"/></button>)}</div>}</CardContent></Card></div></>}
      {view==='projects'&&<Card><CardHeader><CardTitle>Wszystkie projekty</CardTitle><CardDescription>Każdy projekt ma niezależną konfigurację Nginx i opcjonalnie własną bazę.</CardDescription></CardHeader><CardContent>{projects.length===0?<p className="py-8 text-center text-sm text-muted-foreground">Jeszcze nie dodano projektów.</p>:<div className="divide-y">{projects.map(p=><button key={p.slug} className="flex w-full flex-wrap items-center gap-4 py-4 text-left hover:opacity-75" onClick={()=>details(p.slug)}><IconBox><FolderCode className="size-5"/></IconBox><div className="min-w-40 flex-1"><div className="font-medium">{p.name}</div><span className="text-sm text-muted-foreground">{p.domain}</span></div><div className="flex items-center gap-2"><Badge>{p.database?'MariaDB':'Bez bazy'}</Badge>{p.https&&<Dot state="active"/>}<ChevronRight className="size-4 text-muted-foreground"/></div></button>)}</div>}</CardContent></Card>}
      {view==='new'&&<div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]"><Card><CardHeader><CardTitle>Konfiguracja projektu</CardTitle><CardDescription>Przygotuj Nginx, katalog publiczny, opcjonalną bazę i SSL.</CardDescription></CardHeader><CardContent><form className="space-y-5" onSubmit={create}><Field label="Nazwa projektu"><Input required maxLength={70} value={draft.name} onChange={e=>setDraft(d=>({...d,name:e.target.value,slug:d.slug || e.target.value.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}))}/></Field><Field label="Identyfikator katalogu" hint="Katalog: /home/ubuntu/projects/{identyfikator}"><Input value={draft.slug} required pattern="[a-z][a-z0-9-]{1,39}" title="2–40 małych liter, cyfr lub myślników" onChange={e=>setDraft(d=>({...d,slug:e.target.value}))}/></Field><Field label="Domena .test"><Input value={draft.domain} placeholder={`${draft.slug||'projekt'}.test`} onChange={e=>setDraft(d=>({...d,domain:e.target.value}))}/></Field><Field label="Framework" hint="Opcjonalnie użyj oficjalnego instalatora frameworka."><select aria-label="Framework" className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={draft.engine} onChange={e=>{const id=e.target.value;const definition=engines[id];setKitInfo(null);setKitError('');setDraft(d=>({...d,engine:id,starter_kit:definition?.default_option??'none',webroot:definition?.webroot??d.webroot}))}}><option value="none">Bez frameworka — pusty projekt</option>{Object.entries(engines).map(([id,definition])=><option key={id} value={id} disabled={!engineAllowed}>{definition.label}</option>)}</select>{!engineAllowed&&<p className="text-xs text-muted-foreground">Instalatory wymagają logowania lub jednorazowej zgody administratora. Szczegóły w ustawieniach.</p>}</Field>{draftEngine&&<><Field label={draftEngine.option_label} hint={draftEngine.option_hint}><select required aria-label={draftEngine.option_label} disabled={kitLoading||!starterKits.length||engineBusy} className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={kitReady?draft.starter_kit:''} onChange={e=>setDraft(d=>({...d,starter_kit:e.target.value}))}><option value="" disabled>{kitLoading?'Odczytywanie starter kitów…':starterKits.length?'Wybierz starter kit':'Brak dostępnej listy'}</option>{starterKits.map(option=><option key={option.id} value={option.id}>{option.label}</option>)}</select><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted-foreground">{installedKitInfo?.installed?draftEngine.tool+' '+installedKitInfo.installed:''}</p><Button type="button" variant="ghost" size="sm" disabled={kitLoading||engineBusy||!engineAllowed} onClick={()=>{setKitInfo(null);setKitRefresh(v=>v+1)}}><RefreshCw/>Odśwież listę</Button></div>{kitError&&<p role="alert" className="text-xs text-destructive">{kitError}</p>}</Field><p className="text-xs text-muted-foreground">Wymagane narzędzie: {draftEngine.tool}. <button type="button" className="underline" onClick={()=>{setSelectedTool(draft.engine);go('settings')}}>Zarządzaj oficjalnym instalatorem w ustawieniach</button>.</p></>}<Field label="Katalog publiczny" hint="Ścieżka względna projektu"><Input value={draft.webroot} required readOnly={!!draftEngine} onChange={e=>setDraft(d=>({...d,webroot:e.target.value}))}/></Field><div className="space-y-4 rounded-xl border p-4">{draftEngine?.database_options?<Field label="Baza danych" hint={draftEngine.database_hint}><select aria-label="Baza danych" className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring" value={draft.database?'mariadb':'sqlite'} onChange={e=>setDraft(d=>({...d,database:e.target.value==='mariadb'}))}>{draftEngine.database_options.map(option=><option key={option.id} value={option.id}>{option.label}</option>)}</select></Field>:<div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium">Utwórz bazę MariaDB</div><p className="text-xs text-muted-foreground">Oddzielny użytkownik i hasło w panelu.</p></div><Switch aria-label="Utwórz bazę danych" checked={draft.database} onCheckedChange={v=>setDraft(d=>({...d,database:v}))}/></div>}<div className="border-t pt-4"><div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium">Auto SSL (HTTPS)</div><p className="text-xs text-muted-foreground">mkcert i przekierowanie HTTP → HTTPS.</p></div><Switch aria-label="Włącz Auto SSL" checked={draft.https} onCheckedChange={v=>setDraft(d=>({...d,https:v}))}/></div></div></div><Button type="submit" className="w-full" disabled={busy||engineBusy||(!!draftEngine&&(!engineAllowed||!kitReady))}><Plus/>{busy||engineBusy?'Tworzenie…':'Utwórz projekt'}</Button></form></CardContent></Card><div className="space-y-5"><Card><CardHeader><CardTitle>Podgląd</CardTitle><CardDescription>Parametry nowego środowiska</CardDescription></CardHeader><CardContent className="space-y-4"><Info label="Domena" value={`${draft.https?'https':'http'}://${draft.domain||`${draft.slug||'projekt'}.test`}`}/><Info label="Katalog" value={`/home/ubuntu/projects/${draft.slug||'projekt'}/${draft.webroot}`}/><Info label="Framework" value={draftEngine?draftEngine.label+' · '+(starterKits.find(o=>o.id===draft.starter_kit)?.label??'Wybierz starter kit'):'Bez frameworka'}/><Info label="Baza" value={draft.database?(draftEngine?.database_options?'MariaDB · dane w .env':'Nowa MariaDB'):(draftEngine?.database_options?'SQLite':'Bez bazy')}/><Info label="Certyfikat" value={draft.https?'mkcert · Auto SSL':'Wyłączony'}/></CardContent></Card><div className="rounded-xl border bg-secondary/40 p-5 text-sm text-muted-foreground"><ShieldCheck className="mb-3 size-5 text-foreground"/><strong className="text-foreground">Bezpieczne wykonanie</strong><p className="mt-2">Żądanie przejmie odizolowany pomocnik. Po przygotowaniu projektu DevStack zgłosi synchronizację domeny w Windows. DevStack używa standardowych kroków wybranego instalatora, bez dodatkowych migracji.</p></div></div></div>}
      {view==='project'&&current&&<div className="space-y-5"><Button variant="ghost" size="sm" onClick={()=>go('projects')}><ArrowLeft/>Wszystkie projekty</Button><div className="grid gap-5 xl:grid-cols-2"><Card><CardHeader><div className="flex items-center justify-between gap-3"><CardTitle>Projekt</CardTitle>{current.https&&<Dot state="active"/>}</div></CardHeader><CardContent className="space-y-4"><Info label="Domena" value={current.domain} action={<Button variant="ghost" size="icon" aria-label="Otwórz stronę" onClick={()=>window.open(`${current.https?'https':'http'}://${current.domain}`,'_blank','noopener,noreferrer')}><ExternalLink/></Button>}/><Info label="Folder" value={current.path}/><Info label="Webroot" value={current.webroot}/>{current.engine&&current.engine!=='none'&&<><Info label="Framework" value={(engines[current.engine]?.label??current.engine)+' · '+(current.starter_kit_label??(current.starter_kit==='none'?'Blade (bez starter kitu)':current.starter_kit==='livewire'?'Livewire':current.starter_kit??''))}/><Info label="Instalacja" value={current.engine_status==='ready'?'Środowisko gotowe':current.engine_status==='failed'?'Wymaga kontroli':'W toku'}/></>}<Info label="Auto SSL" value={current.https?'Włączone':'Wyłączone'}/></CardContent></Card><Card><CardHeader><CardTitle>{!current.database&&current.engine==='laravel'?'SQLite':'MariaDB'}</CardTitle><CardDescription>{current.engine==='laravel'?(current.database?'Dane połączenia zapisane w pliku .env.':'Lokalny plik bazy aplikacji Laravel.'):'Konto przypisane tylko do tej bazy'}</CardDescription></CardHeader><CardContent className="space-y-4">{current.database?<><Info label="Host" value="127.0.0.1" action={<Button variant="ghost" size="icon" aria-label="Kopiuj host" onClick={()=>{void copy('127.0.0.1')}}><Copy/></Button>}/><Info label="Port" value="3306"/><Info label="Baza" value={current.db_name} action={<Button variant="ghost" size="icon" aria-label="Kopiuj bazę" onClick={()=>{void copy(current.db_name)}}><Copy/></Button>}/><Info label="Użytkownik" value={current.db_user} action={<Button variant="ghost" size="icon" aria-label="Kopiuj użytkownika" onClick={()=>{void copy(current.db_user)}}><Copy/></Button>}/><form onSubmit={reveal} className="rounded-xl border bg-secondary/30 p-3"><Field label={passwordRequired?'Potwierdź hasło panelu, aby zobaczyć hasło bazy':'Pokaż hasło lokalnej bazy'}><div className="flex gap-2">{passwordRequired&&<Input type="password" autoComplete="off" aria-label="Hasło panelu" value={revealPassword} onChange={e=>setRevealPassword(e.target.value)} required/>}<Button type="submit" disabled={busy}>Pokaż</Button></div></Field>{dbSecret&&<div className="mt-3 flex items-center gap-2"><code className="min-w-0 flex-1 select-all break-all rounded-md bg-background p-3 text-xs">{dbSecret}</code><Button size="icon" variant="outline" aria-label="Kopiuj hasło" onClick={()=>{void copy(dbSecret)}}><Copy/></Button><Button size="icon" variant="outline" aria-label="Ukryj hasło" onClick={()=>setDbSecret('')}><X/></Button></div>}<p className="mt-2 text-xs text-muted-foreground">Hasło znika po 20 sekundach. W trybie bez logowania lokalny dostęp do panelu wystarcza do jego ujawnienia.</p></form><Button variant="outline" className="w-full" disabled={busy||testingDb} onClick={()=>{void testDatabase()}}><Database/>{testingDb?'Testowanie MariaDB…':'Sprawdź połączenie, zapis i odczyt MariaDB'}</Button></>:<p className="text-sm text-muted-foreground">{current.engine==='laravel'?'SQLite: database/database.sqlite w katalogu projektu.':'Ten projekt nie ma zarządzanej bazy MariaDB.'}</p>}</CardContent></Card></div>
        <Card><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Konfiguracja Nginx</CardTitle><CardDescription>Reguły wewnątrz <code>location /</code> i dodatkowe lokalizacje zasobów; pełny plik domyślnie ukryty.</CardDescription></div><Badge><FileCode2 className="size-3"/>Zarządzana</Badge></div></CardHeader><CardContent className="space-y-5">
          <Field label="Dodatkowe reguły — location /" hint="Tekst trafia do pierwszego location /. Dozwolone: client_max_body_size (1–999m), client_body_timeout i send_timeout (1–999s). Domyślnego try_files nie wpisuj.">
            <div className="rounded-lg border bg-secondary/40 p-3 font-mono text-xs text-muted-foreground">location / {'{'}</div>
            <Textarea className="min-h-30 font-mono text-xs" spellCheck={false} value={overrides} onChange={e=>setOverrides(e.target.value)} rows={5} placeholder={'client_max_body_size 64m;\nclient_body_timeout 120s;'}/>
            <div className="whitespace-pre-wrap rounded-lg border bg-secondary/40 p-3 font-mono text-xs text-muted-foreground">{'    try_files $uri $uri/ /index.php?$query_string;\n}'}</div>
          </Field>
          <Field label="Dodatkowe lokalizacje zasobów JS/CSS" hint="Osobne bloki location na poziomie server, wstawiane przed regułami PHP. Obsługiwane: bezpieczne wzorce assetów, expires off oraz try_files.">
            <Textarea aria-label="Dodatkowe lokalizacje Nginx" className="min-h-32 font-mono text-xs" spellCheck={false} value={extraLocations} onChange={e=>setExtraLocations(e.target.value)} rows={6}/>
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">Kopia → nginx -t → reload albo cofnięcie.</p><Button disabled={busy || fullEditing} onClick={()=>{void saveConfig()}}><Check/>{busy?'Zapisywanie…':'Sprawdź i zastosuj'}</Button></div>
          <div className="border-t pt-4">
            <Button variant="outline" className="w-full justify-between" onClick={()=>setFullOpen(v=>!v)} aria-expanded={fullOpen}>
              <span className="flex items-center gap-2"><FileCode2 className="size-4"/> {fullOpen?'Ukryj pełną konfigurację':'Pokaż pełną konfigurację'}</span>
              {fullOpen?<ChevronUp className="size-4"/>:<ChevronDown className="size-4"/>}
            </Button>
            {fullOpen&&<div className="mt-4 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">Pełny plik Nginx</h3><p className="text-xs text-muted-foreground">Podgląd i ograniczona edycja zaawansowana.</p></div><Button variant="outline" size="sm" disabled={busy} onClick={toggleFullEdit}><Pencil className="size-4"/>{fullEditing?'Anuluj edycję':'Edytuj'}</Button></div>
              <Textarea aria-label="Pełna konfiguracja Nginx" className="font-mono text-xs leading-relaxed" rows={18} spellCheck={false} value={fullEditing?fullConfig:current.nginx_config} readOnly={!fullEditing} onChange={e=>setFullConfig(e.target.value)}/>
              {fullEditing&&<div className="space-y-3 rounded-lg border p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">Możesz edytować wyłącznie dwa obszary oznaczone znacznikami DevStack: <code>devstack:location:start/end</code> oraz <code>devstack:asset-locations:start/end</code>. Domena, SSL, root i PHP-FPM pozostają chronione.</p>
                {passwordRequired&&<Field label="Potwierdź hasło administratora"><Input type="password" autoComplete="off" value={fullPassword} onChange={e=>setFullPassword(e.target.value)} required/></Field>}
                <div className="flex justify-end"><Button onClick={()=>{void saveFullConfig()}} disabled={busy||(passwordRequired&&!fullPassword)}><Check/>{busy?'Sprawdzanie…':'Sprawdź i zapisz zmiany'}</Button></div>
              </div>}
            </div>}
          </div>
        </CardContent></Card>
        <Card className="border-destructive/35"><CardHeader><CardTitle className="text-destructive">Usuwanie projektu</CardTitle><CardDescription>Usuwa konfigurację Nginx, pulę PHP-FPM i certyfikat projektu. Pliki w katalogu projektu pozostają na dysku.</CardDescription></CardHeader><CardContent className="space-y-4">
          {!deleteOpen?<Button variant="destructive" disabled={busy} onClick={()=>{setDeleteConfirmation('');setDeleteDbConfirm(false);setDeleteOpen(true)}}><Trash2/>Usuń projekt z DevStack</Button>:<form className="space-y-4" onSubmit={deleteProject}>
            <p className="text-sm">Potwierdź usunięcie projektu <strong>{current.name}</strong> (<code>{current.slug}</code>). Nie dotyczy to plików źródłowych w <code>{current.path}</code>.</p>
            {current.database&&<label className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"><input type="checkbox" className="mt-1 size-4 accent-red-600" checked={deleteDbConfirm} onChange={e=>setDeleteDbConfirm(e.target.checked)}/><span><strong>Rozumiem, że baza MariaDB {current.db_name} i jej użytkownik zostaną trwale usunięci.</strong> Dane bazy nie mogą zostać odzyskane bez kopii zapasowej.</span></label>}
            <Field label={`Wpisz dokładnie: ${current.slug}`}><Input autoComplete="off" spellCheck={false} value={deleteConfirmation} onChange={e=>setDeleteConfirmation(e.target.value)} required/></Field>
            <div className="flex flex-wrap gap-2"><Button type="submit" variant="destructive" disabled={busy||deleteConfirmation!==current.slug||(current.database&&!deleteDbConfirm)}><Trash2/>{busy?'Usuwanie…':'Potwierdź usunięcie'}</Button><Button variant="outline" type="button" disabled={busy} onClick={()=>{setDeleteOpen(false);setDeleteConfirmation('');setDeleteDbConfirm(false)}}>Anuluj</Button></div>
          </form>}
        </CardContent></Card></div>}
      {view==='settings'&&<div className="grid gap-5 lg:grid-cols-2"><Card><CardHeader><CardTitle>Parametry środowiska</CardTitle><CardDescription>Utrzymywane na Ubuntu WSL2</CardDescription></CardHeader><CardContent className="space-y-4"><Info label="Panel" value="https://devstack.test"/><Info label="Backend" value="PHP-FPM 8.5 + Unix socket"/><Info label="SSL" value="mkcert (prywatny lokalny CA)"/><Info label="Usługi" value="systemd"/><Info label="Logowanie" value={passwordRequired?'Włączone (hasło)':'Wyłączone (tylko lokalnie)'}/><Info label="Instalatory" value={engineAllowed?(passwordRequired?'Po zalogowaniu':'Dozwolone lokalnie bez hasła'):'Wymagają zgody administratora'}/><p className="text-xs text-muted-foreground">Zmień tryb logowania w Ubuntu poleceniem sudo php /opt/devstack/scripts/configure-auth.php --setup. To ustawienie wymaga uprawnień administratora systemu.</p></CardContent></Card><Card><CardHeader><CardTitle>Synchronizacja Windows</CardTitle><CardDescription>Integracja Windows automatycznie synchronizuje domeny projektów.</CardDescription></CardHeader><CardContent className="space-y-3 text-sm text-muted-foreground"><p>Przy zmianie listy projektów synchronizacja następuje automatycznie po jednorazowej instalacji integracji Windows. Skrypt awaryjny: <code className="rounded bg-secondary px-1 py-0.5">windows/sync-hosts.ps1</code>.</p><p>Integracja aktualizuje tylko zarządzany blok hosts; nie wymaga cyklicznych uruchomień ani ponownego potwierdzania CA. Pierwsza instalacja wymaga zgody administratora Windows.</p><p>Usuwanie projektu jest dostępne w jego szczegółach. Pliki źródłowe nie są usuwane; baza danych wymaga osobnego potwierdzenia. Kopie zapasowe i rotacja haseł nie są jeszcze zautomatyzowane.</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Narzędzia instalacyjne</CardTitle><CardDescription>Oficjalne instalatory frameworków dla użytkownika Ubuntu.</CardDescription></CardHeader><CardContent className="space-y-3">{Object.keys(engines).length>1&&<select aria-label="Instalator" className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={toolId} onChange={e=>setSelectedTool(e.target.value)}>{Object.entries(engines).map(([id,definition])=><option key={id} value={id}>{definition.tool}</option>)}</select>}<Info label="Narzędzie" value={toolDefinition?.tool??'Brak'}/>{toolDefinition&&<p className="text-xs text-muted-foreground">{toolDefinition.description}</p>}{!engineAllowed?<div className="space-y-2 text-xs text-muted-foreground"><p>Aby korzystać z instalatorów bez hasła, administrator Ubuntu może jednorazowo wyrazić zgodę:</p><code className="block break-all rounded bg-secondary p-2">sudo php /opt/devstack/scripts/configure-auth.php --allow-local-installers --i-understand-local-risk</code><p>Odśwież panel po wykonaniu polecenia.</p></div>:<><Info label="Zainstalowana" value={toolLoading?'Odczytywanie…':toolInfo?.installed??(toolInfo?'Brak':'Nieznana')}/><Info label="Dostępna" value={engineJob&&engineJob.engine===toolId&&engineJob.action==='check'&&engineJob.state==='succeeded'?engineJob.tool?.latest??'Nieznana':'Sprawdź aktualizacje'}/>{toolError&&<p role="alert" className="text-xs text-destructive">{toolError}</p>}{toolInfo?.composer===false&&<p className="text-xs text-muted-foreground">Composer nie jest dostępny. Zainstaluj go w Ubuntu.</p>}<div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={busy||engineBusy||toolLoading||!toolInfo?.installed} onClick={()=>void toolAction('check')}><RefreshCw/>Sprawdź aktualizacje</Button><Button variant="outline" size="sm" disabled={busy||engineBusy||toolLoading||!toolInfo?.composer} onClick={()=>void toolAction(toolInfo?.installed?'update-tool':'install-tool')}>{toolInfo?.installed?'Aktualizuj instalator':'Zainstaluj instalator'}</Button></div><p className="text-xs text-muted-foreground">Aktualizacja dotyczy oficjalnego narzędzia instalacyjnego i jego zależności, nie istniejących aplikacji.</p></>}</CardContent></Card>
      <Card><CardHeader><CardTitle>Certyfikat CA w Windows</CardTitle><CardDescription>Pobierz publiczny certyfikat mkcert, gdy konfigurujesz Windows pierwszy raz lub utracono zaufanie CA.</CardDescription></CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">Przeglądarka nie może bezpiecznie samodzielnie dodać CA do zaufanych urzędów Windows. Instalacja wymaga Twojego jawnego potwierdzenia w systemie.</p>
          <Button type="button" variant="outline" onClick={()=>{window.location.assign('/api/ca')}}><ShieldCheck/>Pobierz publiczny certyfikat CA (.cer)</Button>
          <p className="break-all text-xs text-muted-foreground">SHA-256 pobieranego pliku PEM: <code className="font-mono">{caFingerprint||'Odczytywanie…'}</code>{caInfoError?' (Brak dostępu do certyfikatu — sprawdź usługę DevStack.)':''}</p>
          <ol className="list-decimal space-y-1.5 pl-5 text-sm text-muted-foreground">
            <li>Zapisz pobrany <code>devstack-rootCA.cer</code>. W Windows PowerShell sprawdź hash: <code>Get-FileHash -Algorithm SHA256 -LiteralPath "$env:USERPROFILE\\Downloads\\devstack-rootCA.cer"</code>.</li>
            <li>Porównaj hash z wartością powyżej i — przy pierwszej instalacji — także z wynikiem odczytu certyfikatu bezpośrednio z zaufanego Ubuntu: <code>wsl -d Ubuntu-26.04 -u root -- sha256sum /var/lib/devstack/ca/rootCA.pem</code>.</li>
            <li>Uruchom w Windows <code>certmgr.msc</code>, przejdź do <strong>Zaufane główne urzędy certyfikacji → Certyfikaty → Importuj</strong> i wybierz plik .cer. Potwierdź świadomie dodanie zaufanego CA tylko dla Twojego DevStack.</li>
            <li>Uruchom ponownie przeglądarkę lub odśwież stronę po imporcie.</li>
          </ol>
          <p className="text-xs text-muted-foreground">Gdy przeglądarka blokuje otwarcie strony z powodu brakującego CA, użyj wstępnego skryptu <code>windows/setup-windows.ps1</code> zgodnie z instrukcją instalacji. DevStack nie pobiera i nigdy nie udostępnia prywatnego klucza CA.</p>
        </CardContent></Card></div>}
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t pt-5 text-xs text-muted-foreground"><span>DevStack · 0.1.0</span><span>React + shadcn/ui + Tailwind CSS · PHP 8.5 · tylko localhost</span></footer>
      </main></div>
  </div>
}
