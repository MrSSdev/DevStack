import { Children, cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactElement, type ReactNode } from 'react'
import {
  Activity, ArrowLeft, ArrowRight, Check, ChevronRight,
  Copy, Database, ExternalLink, FileCode2, FolderCode, Pencil, ChevronDown, ChevronUp,
  LayoutDashboard, LockKeyhole, LogOut, Mail, Menu, Moon, Plus, RefreshCw,
  Search, Settings2, ShieldCheck, Sun, Trash2, X,
} from 'lucide-react'
import brandIcon from '@/assets/devstack.svg'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { toast, beginNotice, bindOperationToast, notify, fail } from '@/components/ui/toast'
import { cn } from '@/lib/utils'
import { acknowledgeJob, readAcknowledgedJob, rememberVisibleSuccess, showJobNotice, jobNoticeTimeout } from '@/lib/engine-job-notice'
import { frameworkWebroot, resolveInstallerFields, type InstallerField, type InstallerValues } from '@/lib/installer-options'
import { resolveProjectIdentity, projectIdentityPayload, type ProjectIdentityDraft } from '@/lib/project-identity'
import { MailInbox } from '@/components/mail-inbox'
import { NotificationCenter, NotificationSettings, useNotificationCenter } from '@/components/notification-center'
import { acknowledgeOperation, isNoticeRoute, notificationClient, notificationChange, notificationRouteHash, readNoticeOnClose, renewNotificationClient } from '@/lib/notification-client'
import { PageHeader } from '@/components/page-header'
import { ManagedServices, WidgetServiceSettings, type ManagedService } from '@/components/managed-services'

type View = 'dashboard' | 'projects' | 'new' | 'project' | 'mail' | 'settings'
const settingsTabs=[{id:'environment',label:'Środowisko'},{id:'installers',label:'Instalatory'},{id:'windows',label:'Windows'},{id:'widgets',label:'Widżety'},{id:'notifications',label:'Powiadomienia'},{id:'security',label:'Bezpieczeństwo'}] as const
type SettingsTab=typeof settingsTabs[number]['id']

// The native Windows Widgets link to /#/projects and /#/new. Keep this
// routing entirely client-side: no new server endpoints or auth bypass.
function viewFromHash(): View {
  if(/^#\/mail\/[A-Za-z0-9]{22}$/.test(notificationRouteHash()))return 'mail'
  if(/^#\/project\/[a-z][a-z0-9-]{1,39}$/.test(notificationRouteHash()))return 'project'
  switch (notificationRouteHash()) {
    case '#/projects': return 'projects'
    case '#/new': return 'new'
    case '#/mail': return 'mail'
    case '#/settings': return 'settings'
    default: return 'dashboard'
  }
}
type Project = {
  slug: string; name: string; domain: string; path: string; webroot: string
  database: boolean; https: boolean; local_mail?: boolean; db_name: string; db_user: string
  nginx_config: string; overrides: string; extra_locations?: string
  detected_framework?: {name:string;version:string|null}
  engine?: string; starter_kit?: string; starter_kit_label?: string; engine_status?: string
  installer_options?:InstallerValues; installer_option_labels?:{label:string;value:string}[]
}
type StarterKit = { id:string; label:string }
type ToolInfo = { composer: boolean; installed: string|null; latest?: string|null; starter_kits?:StarterKit[]|null; installer_options?:InstallerField[]|null }
type EngineDefinition = { label:string; tool:string; webroot:string; default_option:string; option_label:string; option_hint:string; description:string; database_options?:{id:string;label:string}[]; database_hint?:string }
type EngineJob = { id: string; engine?: string; action: string; state: string; message: string; slug?: string; log?: string; tool?: ToolInfo }
type ServiceState = { services: Record<string,string>; managed_services?:ManagedService[]; service_action_pending?:boolean; mailpit?:string }
type State = ServiceState & { engine_notice?:{id:string;show:boolean}|null; projects: Project[]; preferences: { theme: 'light'|'dark' }; engine_job?: EngineJob|null; engines?: Record<string,EngineDefinition>; engine_access_allowed?: boolean }
function serviceFields(state:ServiceState):ServiceState {
  return {services:state.services,managed_services:state.managed_services,service_action_pending:state.service_action_pending,mailpit:state.mailpit}
}
type Envelope<T> = { notification_id?:string; ok: boolean; result?: T; error?: string; authenticated?: boolean; csrf?: string }
type Draft = ProjectIdentityDraft & { webroot: string; database: boolean; https: boolean; local_mail: boolean; engine: string; starter_kit: string }

// Render the two stored regions in one editor without losing legacy rules.
function nginxRules(config?: {overrides?:string;extra_locations?:string}) {
  return [config?.overrides?.trim(),config?.extra_locations?.trim()].filter(Boolean).join('\n\n')
}

const blank: Draft = { name: '', slug: null, domain: null, webroot: 'public', database: true, https: true, local_mail: false, engine: 'none', starter_kit: 'none' }

async function request<T>(method: 'GET'|'POST', path: string, csrf?: string, data?: unknown, operationToastId?:string, signal?:AbortSignal): Promise<T> {
  const client=notificationClient
  const response = await fetch(path, {
    method, credentials: 'same-origin', cache: 'no-store', signal,
    headers: { 'X-DevStack-Client':client, ...(data ? {'Content-Type':'application/json'} : {}), ...(method === 'POST' ? {'X-CSRF-Token':csrf ?? ''} : {}) },
    ...(data ? {body:JSON.stringify(data)} : {}),
  })
  let body: Envelope<T>
  try { body = await response.json() as Envelope<T> }
  catch { throw new Error(`Nieprawidłowa odpowiedź serwera (HTTP ${response.status}).`) }
  if(method==='POST'&&csrf){
    const result=body.result as {notification_id?:string}|undefined
    const id=result?.notification_id??body.notification_id
    if(operationToastId)bindOperationToast(operationToastId,id,client)
    acknowledgeOperation(id,csrf,client)
    if(result?.notification_id?.startsWith('job-'))window.dispatchEvent(new Event(notificationChange))
  }
  if (!response.ok || !body.ok) throw new Error(body.error || `Błąd HTTP ${response.status}.`)
  return (body.result ?? body) as T
}

function projectFramework(project:Project):string {
  const detected=project.detected_framework
  if(detected)return detected.name+(detected.version?' '+detected.version:' · wersja nieznana')
  if(project.engine==='laravel')return 'Laravel · wersja nieznana'
  return 'Nie wykryto frameworka'
}
function FrameworkBadge({project}:{project:Project}){
  return <Badge className="max-w-full whitespace-normal break-words" title={project.detected_framework?'Wersja z zainstalowanych pakietów Composer':'Brak potwierdzonej wersji z zainstalowanych pakietów'}>{projectFramework(project)}</Badge>
}
function HttpsBadge() {
  return <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300"><LockKeyhole className="size-3"/>HTTPS</Badge>
}
function Field({label,hint,children}:{label:string;hint?:string;children:ReactNode}) {
  const id=useId()
  const items=Children.toArray(children)
  const control=items.find(child=>isValidElement(child)&&(child.type===Input||child.type===Textarea||child.type==='select'))
  return <div className="space-y-1.5">{control?<label htmlFor={id} className="block text-sm font-medium">{label}</label>:<div className="text-sm font-medium">{label}</div>}{items.map(child=>child===control?cloneElement(child as ReactElement<{id?:string}>,{id}):child)}{hint&&<p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}</div>
}
function IconBox({children}:{children:ReactNode}) {
  return <span className="flex size-8 shrink-0 items-center justify-center rounded-xl border bg-secondary text-secondary-foreground">{children}</span>
}
function Metric({label,value,icon,foot}:{label:string;value:string;icon:ReactNode;foot:string}) {
  return <Card><CardContent className="p-3"><div className="flex items-start justify-between gap-2"><span className="text-sm font-medium text-muted-foreground">{label}</span><span className="text-muted-foreground">{icon}</span></div><div className="mt-2 text-2xl font-semibold tracking-tight">{value}</div><p className="mt-1 text-xs text-muted-foreground">{foot}</p></CardContent></Card>
}
function Info({label,value,action,stacked=false}:{label:string;value:string;action?:ReactNode;stacked?:boolean}) {
  return <div className={cn('flex min-w-0',stacked?'flex-col gap-1':'min-h-7 items-center gap-3')}><span className={cn('text-xs text-muted-foreground',!stacked&&'w-26 shrink-0')}>{label}</span><span className="min-w-0 flex-1 break-all text-sm font-medium">{value}</span>{action}</div>
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
  const serviceReadRef=useRef(false)
  const serviceSequence=useRef(0)
  const appliedServiceSequence=useRef(0)
  const [serviceStatusUnavailable,setServiceStatusUnavailable]=useState(false)
  const [navOpen,setNavOpen]=useState(false)
  const [desktop,setDesktop]=useState(()=>window.matchMedia('(min-width: 1024px)').matches)
  const navigation=useRef<HTMLElement>(null)
  const menuButton=useRef<HTMLButtonElement>(null)
  const [view,setView]=useState<View>(viewFromHash)
  const [mailRoute,setMailRoute]=useState(()=>notificationRouteHash().match(/^#\/mail\/([A-Za-z0-9]{22})$/)?.[1]??'')
  const [settingsTab,setSettingsTab]=useState<SettingsTab>('environment')
  const [server,setServer]=useState<State>({services:{},projects:[],preferences:{theme:'light'}})
  const [selected,setSelected]=useState(()=>notificationRouteHash().match(/^#\/project\/([a-z][a-z0-9-]{1,39})$/)?.[1]??'')
  const [projectSearch,setProjectSearch]=useState('')
  const jobPresentation=useRef<{signature:string;state:string}|null>(null)
  const activeEngineToast=useRef<{jobId:string;toastId:string}|null>(null)
  const [awaitingEngineJob,setAwaitingEngineJob]=useState('')
  const closingSession=useRef(false)
  const [busy,setBusy]=useState(false)
  const [draft,setDraft]=useState<Draft>(blank)
  const [selectedTool,setSelectedTool]=useState('')
  const [dismissedJob,setDismissedJob]=useState(readAcknowledgedJob)
  const [toolInfo,setToolInfo]=useState<ToolInfo|null>(null)
  const [toolLoading,setToolLoading]=useState(false)
  const [kitInfo,setKitInfo]=useState<{engine:string; info:ToolInfo}|null>(null)
  const [kitLoading,setKitLoading]=useState(false)
  const [kitRefresh,setKitRefresh]=useState(0)
  const [installerChoices,setInstallerChoices]=useState<InstallerValues>({})
  const [rulesDraft,setRulesDraft]=useState<{slug:string;value:string}|null>(null)
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

  function navigateNotice(route:string){
    if(!isNoticeRoute(route))return
    const hash=route.slice(1)
    if(window.location.hash!==hash)window.location.hash=hash
    else {setView(viewFromHash());setMailRoute(hash.match(/^#\/mail\/([A-Za-z0-9]{22})$/)?.[1]??'');const slug=hash.match(/^#\/project\/([a-z][a-z0-9-]{1,39})$/)?.[1];if(slug)setSelected(slug)}
  }
  const notifications=useNotificationCenter(authenticated,csrf,request,navigateNotice)
  const projects=server.projects
  const searchTerms=projectSearch.trim().toLocaleLowerCase('pl-PL').split(/\s+/).filter(Boolean)
  const visibleProjects=projects.filter(p=>{const haystack=[p.name,p.slug,p.domain,projectFramework(p)].join(' ').toLocaleLowerCase('pl-PL');return searchTerms.every(term=>haystack.includes(term))})
  // Never present the last known runtime state as live after a failed read.
  const managed=(server.managed_services??[]).map(s=>serviceStatusUnavailable?{...s,state:'unknown'}:s)
  const monitorServices=view==='dashboard'||view==='mail'||view==='new'||(view==='settings'&&settingsTab==='widgets')
  const engineJob=server.engine_job
  const engines=server.engines??{}
  const engineAllowed=server.engine_access_allowed===true
  const draftEngine=engines[draft.engine]
  const installedKitInfo=kitInfo?.engine===draft.engine?kitInfo.info:null
  const starterKits=installedKitInfo?.starter_kits??[]
  const kitReady=!kitLoading&&Array.isArray(installedKitInfo?.installer_options)&&starterKits.some(o=>o.id===draft.starter_kit)
  const installerFields=installedKitInfo?.installer_options??[]
  const resolvedOptions=resolveInstallerFields(installerFields,draft.starter_kit,installerChoices)
  const effectiveWebroot=frameworkWebroot(draftEngine,draft.webroot)
  const identity=resolveProjectIdentity(draft)
  const toolId=engines[selectedTool]?selectedTool:Object.keys(engines)[0]??''
  const toolDefinition=engines[toolId]
  const engineBusy=!!awaitingEngineJob || (!!engineJob && !['succeeded','failed'].includes(engineJob.state))
  const current=projects.find(p=>p.slug===selected)
  // Display persisted rules immediately; an edit belongs to one project only.
  // Do not clear or rehydrate the editor in a post-render effect.
  const rules=rulesDraft&&rulesDraft.slug===current?.slug?rulesDraft.value:nginxRules(current)
  const title:Record<View,string>={dashboard:'Przegląd',projects:'Projekty',new:'Nowy projekt',project:current?.name??'Projekt',mail:'E-mail',settings:'Ustawienia'}

  const refresh=useCallback(async(manual=false)=>{
    if(refreshRef.current)return
    refreshRef.current=true
    const sequence=++serviceSequence.current
    const client=notificationClient
    const pending=manual?beginNotice('Odświeżanie danych…'):undefined
    if(manual)setRefreshing(true)
    try {
      const next=await request<State>('GET','/api/state')
      if(client!==notificationClient||closingSession.current)return
      // Full refresh may have started before a confirmed service mutation
      // or a newer lightweight read. Do not replace their newer status.
      if(sequence>=appliedServiceSequence.current){
        appliedServiceSequence.current=sequence
        setServiceStatusUnavailable(false)
        setServer(next)
      }else{
        setServer(prev=>({...next,...serviceFields(prev)}))
      }
      if(!themeSaveRef.current && (next.preferences?.theme==='dark'||next.preferences?.theme==='light'))setTheme(next.preferences.theme)
      setLastRefreshed(new Date())
      toast.close('state-refresh-error')
      if(manual)notify('Odświeżono dane.',pending)
    }catch(e){
      if(client===notificationClient&&!closingSession.current)fail(e,pending??'state-refresh-error','Nie udało się odświeżyć danych.')
    }finally{
      refreshRef.current=false
      if(manual)setRefreshing(false)
    }
  },[])
  async function toggleTheme(){
    if(savingTheme || themeSaveRef.current)return
    const next=theme==='light'?'dark':'light'
    const pending=crypto.randomUUID()
    themeSaveRef.current=true
    setSavingTheme(true)
    try{
      const saved=await request<{theme:'light'|'dark'}>('POST','/api/preferences',csrf,{theme:next},pending)
      setTheme(saved.theme)
      setServer(prev=>({...prev,preferences:{theme:saved.theme}}))
      notify(saved.theme==='dark'?'Zapisano ciemny motyw na serwerze.':'Zapisano jasny motyw na serwerze.',pending)
    }catch(e){
      fail(e,pending,'Nie udało się zapisać motywu.')
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
  useEffect(()=>{window.scrollTo(0,0)},[view])
  useEffect(()=>{
    const media=window.matchMedia('(min-width: 1024px)')
    const resize=()=>{setDesktop(media.matches);if(media.matches)setNavOpen(false)}
    media.addEventListener('change',resize)
    return ()=>media.removeEventListener('change',resize)
  },[])
  useEffect(()=>{
    if(!navOpen||desktop)return
    const previousOverflow=document.body.style.overflow
    document.body.style.overflow='hidden'
    navigation.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const keydown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();setNavOpen(false)}
      if(event.key!=='Tab')return
      const buttons=navigation.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])')
      if(!buttons?.length)return
      const first=buttons[0],last=buttons[buttons.length-1]
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    }
    document.addEventListener('keydown',keydown)
    return ()=>{
      document.body.style.overflow=previousOverflow
      document.removeEventListener('keydown',keydown)
      if(!window.matchMedia('(min-width: 1024px)').matches)menuButton.current?.focus()
    }
  },[navOpen,desktop])
  useEffect(()=>{
    // Support direct bookmarks from Windows Widgets, plus browser Back/Forward.
    const syncRoute=()=>{setView(viewFromHash());setNavOpen(false);setDbSecret('');setMailRoute(notificationRouteHash().match(/^#\/mail\/([A-Za-z0-9]{22})$/)?.[1]??'');const slug=notificationRouteHash().match(/^#\/project\/([a-z][a-z0-9-]{1,39})$/)?.[1];if(slug)setSelected(slug)}
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
      .catch(()=>{if(!disposed){setCaInfoError(true);fail('Brak dostępu do certyfikatu — sprawdź usługę DevStack.','ca-info-error')}})
    return ()=>{disposed=true}
  },[authenticated,view])
  useEffect(()=>{
    if(!authenticated)return
    const id=window.setInterval(()=>{void refresh()},30000)
    return ()=>window.clearInterval(id)
  },[authenticated,refresh])
  useEffect(()=>{
    if(!authenticated||initializing||!monitorServices)return
    let disposed=false
    let timer:number|undefined
    let controller:AbortController|undefined
    const cancelTimer=()=>{if(timer!==undefined){window.clearTimeout(timer);timer=undefined}}
    const schedule=(delay:number)=>{
      if(!disposed&&document.visibilityState==='visible')timer=window.setTimeout(()=>{void poll()},delay)
    }
    async function poll(){
      cancelTimer()
      if(disposed||document.visibilityState!=='visible'||closingSession.current)return
      // One fast request at a time, including view changes. A full state
      // refresh already underway has priority on the serial Unix helper.
      if(serviceReadRef.current||refreshRef.current){schedule(2000);return}
      serviceReadRef.current=true
      const sequence=++serviceSequence.current
      const client=notificationClient
      const abort=new AbortController()
      controller=abort
      const timeout=window.setTimeout(()=>abort.abort(),8000)
      let delay=2000
      try{
        const next=await request<ServiceState>('GET','/api/services/status',undefined,undefined,undefined,abort.signal)
        if(!disposed&&client===notificationClient&&!closingSession.current&&sequence>=appliedServiceSequence.current){
          if(!Array.isArray(next.managed_services)||typeof next.service_action_pending!=='boolean'||typeof next.mailpit!=='string'||!next.services)throw new Error('Niepełny stan usług.')
          appliedServiceSequence.current=sequence
          setServer(prev=>({...prev,...serviceFields(next)}))
          setServiceStatusUnavailable(false)
        }
      }catch{
        // A disconnected WSL or unavailable helper must not generate a toast
        // every two seconds. Display unknown status, then back off.
        delay=5000
        if(!disposed&&client===notificationClient&&!closingSession.current&&sequence>=appliedServiceSequence.current){
          appliedServiceSequence.current=sequence
          setServiceStatusUnavailable(true)
        }
      }finally{
        window.clearTimeout(timeout)
        serviceReadRef.current=false
        controller=undefined
        schedule(delay)
      }
    }
    const resume=()=>{
      if(document.visibilityState==='visible')void poll()
      else cancelTimer()
    }
    resume()
    window.addEventListener('focus',resume)
    document.addEventListener('visibilitychange',resume)
    return ()=>{
      disposed=true
      cancelTimer()
      controller?.abort()
      window.removeEventListener('focus',resume)
      document.removeEventListener('visibilitychange',resume)
    }
  },[authenticated,initializing,monitorServices])
  useEffect(()=>{
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
    e.preventDefault();setBusy(true);toast.close('login-error')
    try {
      const info=await request<{authenticated:boolean;csrf:string}>('POST','/api/login',csrf,{password:loginPassword})
      renewNotificationClient();closingSession.current=false;setDismissedJob(readAcknowledgedJob());setCsrf(info.csrf);setAuthenticated(true);setLoginPassword('');await refresh()
    }catch(e){fail(e,'login-error')}finally{setBusy(false)}
  }
  async function logout(){
    try {const info=await request<{csrf:string}>('POST','/api/logout',csrf,{});setCsrf(info.csrf)}
    catch(e){fail(e);return}
    closingSession.current=true;jobPresentation.current=null;activeEngineToast.current=null;setAwaitingEngineJob('');renewNotificationClient();toast.close()
    appliedServiceSequence.current=++serviceSequence.current
    setServiceStatusUnavailable(false)
    setNavOpen(false);setAuthenticated(false);setDbSecret('');setProjectSearch('');setRulesDraft(null);setServer({services:{},projects:[],preferences:{theme:'light'}});setSelected('');setView('dashboard')
  }
  useEffect(()=>{
    if(!authenticated||initializing)return
    const job=engineJob
    // A GET already in flight can still contain the old job when POST returns.
    // Keep polling the accepted ID instead of retiring the new loading toast.
    if(awaitingEngineJob&&job?.id!==awaitingEngineJob)return
    if(awaitingEngineJob)setAwaitingEngineJob('')
    // Real job presentation is assigned by the shared journal. Keep a local
    // already-started toast alive if its history entry was deliberately cleared.
    if(job&&server.engine_notice?.id===job.id&&!server.engine_notice.show&&activeEngineToast.current?.jobId!==job.id)return
    const jobId=job?.id??''
    if((activeEngineToast.current?.jobId??'')!==jobId){
      const previous=activeEngineToast.current
      activeEngineToast.current=job?{jobId,toastId:'engine-'+jobId}:null
      jobPresentation.current=null
      if(previous)toast.close(previous.toastId)
    }
    if(!job||!showJobNotice(job,dismissedJob))return
    const id=activeEngineToast.current!.toastId
    const running=!['succeeded','failed'].includes(job.state)
    const title=job.action==='create'?'Instalacja projektu':engines[job.engine??'']?.tool??'Instalator'
    const canOpen=!running&&!!job.slug&&projects.some(p=>p.slug===job.slug)
    const description=[job.slug||'Narzędzia instalacyjne',job.message,
      job.log?'Prywatny log w Ubuntu: ~/.cache/devstack/engines/'+job.log:'',
      job.state==='failed'?'Nie ponawiamy instalacji automatycznie. Sprawdź stan projektu i log przed kolejną próbą.':''].filter(Boolean).join('\n')
    const signature=JSON.stringify([job.id,job.state,title,description,canOpen])
    if(jobPresentation.current?.signature!==signature){
      const previousState=jobPresentation.current?.state
      jobPresentation.current={signature,state:job.state}
      const readCompleted=running?undefined:readNoticeOnClose('job-'+job.id)
      const options={title,description,type:running?'loading':job.state==='succeeded'?'success':'error',
        actionProps:canOpen?{children:'Przejdź do projektu',onClick:()=>{details(job.slug!);toast.close(id)}}:undefined,
        onClose:()=>{if(!closingSession.current&&activeEngineToast.current?.jobId===job.id&&!running){setDismissedJob(acknowledgeJob(job));readCompleted?.()}}}
      // A new state starts its own timeout. Metadata changes within the same
      // terminal state must not restart that timeout on every refresh.
      if(previousState===job.state)toast.update(id,options)
      else toast.add({id,...options,timeout:jobNoticeTimeout(job)})
    }
    if(job.state!=='succeeded')return
    const remember=()=>rememberVisibleSuccess(job,document.visibilityState==='visible')
    remember()
    document.addEventListener('visibilitychange',remember)
    return ()=>document.removeEventListener('visibilitychange',remember)
  },[authenticated,initializing,engineJob,dismissedJob,awaitingEngineJob,engines,projects,server.engine_notice])
  useEffect(()=>{
    if(!authenticated || !server.service_action_pending || monitorServices)return
    const id=window.setInterval(()=>{if(document.visibilityState==='visible')void refresh()},1500)
    return ()=>window.clearInterval(id)
  },[authenticated,server.service_action_pending,monitorServices,refresh])
  useEffect(()=>{
    if(!authenticated || !engineBusy)return
    const id=window.setInterval(()=>{if(document.visibilityState==='visible')void refresh()},1500)
    return ()=>window.clearInterval(id)
  },[authenticated,engineBusy,refresh])
  useEffect(()=>{
    if(!authenticated || !engineAllowed || view!=='settings' || engineBusy || !toolId)return
    let disposed=false
    setToolLoading(true);setToolInfo(null)
    void request<ToolInfo>('GET','/api/engines/'+encodeURIComponent(toolId))
      .then(info=>{if(!disposed){setToolInfo(info);toast.close('tool-status-error')}})
      .catch(e=>{if(!disposed)fail(e,'tool-status-error','Błąd odczytu instalatora.')})
      .finally(()=>{if(!disposed)setToolLoading(false)})
    return ()=>{disposed=true}
  },[authenticated,engineAllowed,view,engineBusy,engineJob?.id,toolId])
  useEffect(()=>{
    if(!authenticated || !engineAllowed || view!=='new' || engineBusy || draft.engine==='none'){
      setKitInfo(null);setKitLoading(false)
      return
    }
    const id=draft.engine
    let disposed=false
    setKitInfo(null);setKitLoading(true)
    void request<ToolInfo>('GET','/api/engines/'+encodeURIComponent(id))
      .then(info=>{
        if(disposed)return
        setKitInfo({engine:id,info})
        if(info.installed && (!info.starter_kits?.length||!Array.isArray(info.installer_options)))fail('Nie udało się odczytać opcji aktualnego instalatora.','kit-status-error')
        else if(!info.installed)fail('Najpierw zainstaluj oficjalny instalator w ustawieniach.','kit-status-error')
        else toast.close('kit-status-error')
        setDraft(prev=>prev.engine===id && info.starter_kits?.length && !info.starter_kits.some(o=>o.id===prev.starter_kit)
          ? {...prev,starter_kit:''}:prev)
        setInstallerChoices(prev=>Object.fromEntries(Object.entries(prev).filter(([key,value])=>
          info.installer_options?.some(field=>field.id===key&&field.choices.some(choice=>choice.id===value)))))
      })
      .catch(e=>{if(!disposed)fail(e,'kit-status-error','Nie udało się odczytać starter kitów.')})
      .finally(()=>{if(!disposed)setKitLoading(false)})
    return ()=>{disposed=true}
  },[authenticated,engineAllowed,view,engineBusy,engineJob?.id,draft.engine,kitRefresh])
  function acceptEngineJob(jobId:string,toastId:string,title:string,description:string){
    const previous=activeEngineToast.current
    activeEngineToast.current={jobId,toastId}
    jobPresentation.current=null
    if(previous&&previous.toastId!==toastId)toast.close(previous.toastId)
    // Keep the exact same toast/DOM node from submission through polling.
    toast.update(toastId,{title,description,type:'loading',timeout:0})
    setAwaitingEngineJob(jobId)
  }
  async function toolAction(action:'check'|'install-tool'|'update-tool'){
    if(busy || engineBusy || toolLoading || !toolId)return
    setBusy(true)
    const pending=beginNotice(toolDefinition?.tool??'Instalator','Zlecanie operacji…')
    setAwaitingEngineJob('submitting')
    try{
      const result=await request<{message:string;job_id:string}>('POST','/api/engines/'+encodeURIComponent(toolId),csrf,{action},pending)
      acceptEngineJob(result.job_id,pending,toolDefinition?.tool??'Instalator',result.message)
      await refresh()
    }catch(e){setAwaitingEngineJob('');fail(e,pending)}finally{setBusy(false)}
  }
  async function create(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    if(busy||engineBusy||(!!draftEngine&&(!engineAllowed||!kitReady)))return
    setBusy(true)
    const pending=beginNotice(draftEngine?'Instalacja projektu':'Tworzenie projektu',identity.slug)
    if(draftEngine)setAwaitingEngineJob('submitting')
    try {
      const payload={...draft,...projectIdentityPayload(draft),webroot:draftEngine?undefined:draft.webroot.trim(),installer_options:draftEngine?resolvedOptions.values:{}}
      const answer=await request<{message:string;job_id?:string}>('POST','/api/projects',csrf,payload,pending)
      if(answer.job_id)acceptEngineJob(answer.job_id,pending,'Instalacja projektu',payload.slug+'\n'+answer.message)
      else setAwaitingEngineJob('')
      await refresh();setDraft(blank);setInstallerChoices({})
      if(!answer.job_id){setRulesDraft(null);setSelected(payload.slug);setView('project');notify(answer.message,pending)}
    }catch(e){setAwaitingEngineJob('');fail(e,pending)}finally{setBusy(false)}
  }
  async function testDatabase(){
    if(!current || !current.database || busy || testingDb)return
    setTestingDb(true)
    const pending=beginNotice('Sprawdzanie połączenia z bazą…',current.slug)
    try{
      const result=await request<{message:string}>('POST','/api/projects/test-db',csrf,{slug:current.slug},pending)
      notify(result.message,pending)
    }catch(e){fail(e,pending)}finally{setTestingDb(false)}
  }
  async function deleteProject(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    if(!current || deleteConfirmation!==current.slug || (current.database&&!deleteDbConfirm))return
    setBusy(true)
    const pending=beginNotice('Usuwanie projektu…',current.slug)
    try{
      const result=await request<{message:string}>('POST','/api/projects/delete',csrf,{
        slug:current.slug,confirmation:deleteConfirmation,delete_database:current.database && deleteDbConfirm
      },pending)
      setDeleteOpen(false)
      setDeleteConfirmation('')
      setDeleteDbConfirm(false)
      setRulesDraft(null)
      setSelected('')
      setView('projects')
      setDbSecret('')
      await refresh()
      notify(result.message,pending)
    }catch(e){fail(e,pending)}finally{setBusy(false)}
  }
  async function saveConfig(){
    if(!current)return;setBusy(true)
    const pending=beginNotice('Sprawdzanie konfiguracji Nginx…',current.slug)
    try {
      const result=await request<{message:string;overrides:string;extra_locations:string}>('POST','/api/config',csrf,{slug:current.slug,rules},pending)
      setRulesDraft({slug:current.slug,value:nginxRules(result)})
      await refresh();notify(result.message,pending)
    }catch(e){fail(e,pending)}finally{setBusy(false)}
  }
  async function saveFullConfig(){
    if(!current || (passwordRequired && !fullPassword))return
    setBusy(true)
    const pending=beginNotice('Sprawdzanie konfiguracji Nginx…',current.slug)
    try {
      const result=await request<{message:string;overrides:string;extra_locations:string}>('POST','/api/config/full',csrf,{slug:current.slug,full_config:fullConfig,password:fullPassword},pending)
      setRulesDraft({slug:current.slug,value:nginxRules(result)})
      setFullPassword('')
      setFullEditing(false)
      await refresh()
      notify(result.message,pending)
    }catch(e){fail(e,pending)}finally{setBusy(false)}
  }
  function toggleFullEdit(){
    if(!current)return
    if(fullEditing){setFullEditing(false);setFullConfig(current.nginx_config);setFullPassword('');return}
    if(rules!==nginxRules(current)){fail(new Error('Najpierw zapisz lub cofnij niezapisane zmiany reguł Nginx.'));return}
    setFullConfig(current.nginx_config)
    setFullPassword('')
    setFullEditing(true)
  }
  async function reveal(e:FormEvent<HTMLFormElement>){
    e.preventDefault();if(!current)return;setBusy(true)
    try {
      const result=await request<{password:string}>('POST','/api/reveal',csrf,{slug:current.slug,password:revealPassword})
      setDbSecret(result.password);setRevealPassword('');notify('Hasło wyświetlone na 20 sekund. Nie zapisujemy go w przeglądarce.')
    }catch(e){fail(e)}finally{setBusy(false)}
  }
  async function copy(value:string){
    try {await navigator.clipboard.writeText(value);notify('Skopiowano do schowka.')}
    catch {fail('Przeglądarka zablokowała schowek.')}
  }
  function go(next:View){
    setView(next);setNavOpen(false);setDbSecret('');if(next==='mail')setMailRoute('')
    // Keep a selected project's in-memory detail view as-is. All other
    // views have stable, shareable hash URLs used by Windows Widgets.
    if(next!=='project')window.location.hash='/'+next
  }
  function details(slug:string){setRulesDraft(null);setSelected(slug);go('project')}
  const linkStyle=(active:boolean)=>cn('flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',active?'bg-primary text-primary-foreground':'text-muted-foreground hover:bg-secondary hover:text-foreground')

  if(initializing) return <div className="flex min-h-screen items-center justify-center gap-3 text-sm text-muted-foreground"><RefreshCw className="size-5 animate-spin"/>Uruchamianie DevStack…</div>
  if(!authenticated) return <div className="flex min-h-screen items-center justify-center bg-background p-6"><div className="w-full max-w-sm space-y-6"><div className="flex justify-center"><img src={brandIcon} alt="" className="size-14"/></div><div className="text-center"><h1 className="text-3xl font-semibold tracking-tight">DevStack</h1><p className="mt-2 text-sm text-muted-foreground">Twoje lokalne środowisko programistyczne.</p></div><Card><CardHeader><CardTitle>Logowanie</CardTitle><CardDescription>Wprowadź hasło panelu ustawione podczas instalacji.</CardDescription></CardHeader><CardContent><form onSubmit={login} className="space-y-3"><Field label="Hasło administratora"><Input type="password" autoComplete="current-password" minLength={1} value={loginPassword} onChange={e=>setLoginPassword(e.target.value)} required autoFocus/></Field><Button className="w-full" type="submit" disabled={busy}>{busy?'Sprawdzanie…':'Zaloguj się'}<ArrowRight/></Button></form></CardContent></Card><p className="text-center text-xs text-muted-foreground"><LockKeyhole className="mr-1 inline size-3"/> Tylko lokalnie · HTTPS · WSL2</p></div></div>
  return <div className="min-h-dvh bg-background">
    {navOpen&&<button type="button" aria-label="Zamknij menu" className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={()=>setNavOpen(false)}/>}
    <aside ref={navigation} id="main-navigation" inert={!desktop&&!navOpen} role={!desktop&&navOpen?'dialog':undefined} aria-modal={!desktop&&navOpen?true:undefined} aria-label="Menu główne" className={cn('fixed inset-y-0 left-0 z-40 flex w-52 flex-col border-r bg-card px-3 py-3 transition-transform lg:translate-x-0',navOpen?'translate-x-0':'-translate-x-full')}>
      <div className="flex items-center justify-between px-1 pb-5"><div className="flex items-center gap-3"><img src={brandIcon} alt="" className="size-10 shrink-0"/><div><div className="font-semibold tracking-tight">DevStack</div><div className="text-[11px] text-muted-foreground">WSL2</div></div></div><Button size="icon" variant="ghost" className="lg:hidden" aria-label="Zamknij menu" onClick={()=>setNavOpen(false)}><X/></Button></div>
      <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Środowisko</p>
      <nav className="space-y-1" aria-label="Nawigacja główna"><button onClick={()=>go('dashboard')} className={linkStyle(view==='dashboard')}><LayoutDashboard className="size-4"/>Przegląd</button><button onClick={()=>go('projects')} className={linkStyle(view==='projects'||view==='project'||view==='new')}><FolderCode className="size-4"/>Projekty <span className="ml-auto text-xs opacity-70">{projects.length}</span></button><button onClick={()=>go('mail')} className={linkStyle(view==='mail')}><Mail className="size-4"/>E-mail</button></nav>
      <p className="px-3 pb-2 pt-5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">System</p><button onClick={()=>go('settings')} className={linkStyle(view==='settings')}><Settings2 className="size-4"/>Ustawienia</button>
      <div className="mt-auto space-y-3 rounded-xl border bg-secondary/50 p-3"><div className="flex items-center gap-2 text-xs font-medium"><ShieldCheck className="size-4 text-emerald-600"/> Lokalnie · WSL2</div><div className="break-all text-xs text-muted-foreground">https://devstack.test</div>{passwordRequired?<Button className="w-full" variant="outline" size="sm" onClick={()=>{void logout()}}><LogOut/>Wyloguj</Button>:<Badge>Tryb bez hasła</Badge>}</div>
    </aside>
    <div className="flex min-h-dvh min-w-0 flex-col lg:pl-52" inert={!desktop&&navOpen}><header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur"><div className="mx-auto flex h-12 max-w-7xl items-center justify-between gap-2 px-3 sm:px-5"><div className="flex min-w-0 items-center gap-2"><Button size="icon" variant="ghost" ref={menuButton} className="lg:hidden" aria-label="Otwórz menu" aria-expanded={navOpen} aria-controls="main-navigation" onClick={()=>setNavOpen(true)}><Menu/></Button><a href="#/dashboard" onClick={e=>{e.preventDefault();go('dashboard')}} className="truncate rounded-sm text-sm font-semibold hover:underline focus-visible:outline-2 focus-visible:outline-ring" title="Strona główna">DevStack</a></div><div className="flex items-center gap-2"><Badge className="hidden md:inline-flex">Ubuntu 26.04 · WSL2</Badge><span className="hidden text-xs text-muted-foreground xl:inline" aria-live="polite">{lastRefreshed?'Aktualizacja: '+lastRefreshed.toLocaleTimeString('pl-PL',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'Oczekiwanie na dane'}</span><NotificationCenter model={notifications} navigate={navigateNotice}/><Button variant="outline" size="icon" aria-label={refreshing?'Odświeżanie danych':'Odśwież dane usług i projektów'} title={refreshing?'Odświeżanie…':'Odśwież dane usług i projektów'} disabled={refreshing} onClick={()=>{void refresh(true)}}><RefreshCw className={refreshing?'animate-spin':''}/></Button><Button variant="outline" size="icon" aria-label={theme==='light'?'Włącz ciemny motyw':'Włącz jasny motyw'} title={savingTheme?'Zapisywanie motywu…':'Zmień i zapisz motyw'} disabled={savingTheme} onClick={()=>{void toggleTheme()}}>{theme==='light'?<Moon/>:<Sun/>}</Button></div></div></header>
      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-3 px-3 py-4 sm:px-5 sm:py-5">
      {view!=='mail'&&<><PageHeader title={title[view]} actions={<>{view==='projects'&&<Button size="sm" onClick={()=>go('new')}><Plus/>Nowy projekt</Button>}{view==='new'&&<div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={()=>go('projects')}><ArrowLeft/>Projekty</Button><Button size="sm" type="submit" form="new-project-form" disabled={busy||engineBusy||(draft.local_mail&&(serviceStatusUnavailable||server.mailpit!=='active'))||(!!draftEngine&&(!engineAllowed||!kitReady))}><Plus/>{busy||engineBusy?'Tworzenie…':'Utwórz projekt'}</Button></div>}{view==='project'&&current&&<div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={()=>go('projects')}><ArrowLeft/>Projekty</Button><Button size="sm" variant="outline" onClick={()=>{void copy(`${current.https?'https':'http'}://${current.domain}`)}}><Copy/>URL</Button><Button size="sm" onClick={()=>window.open(`${current.https?'https':'http'}://${current.domain}`,'_blank','noopener,noreferrer')}><ExternalLink/>Otwórz stronę</Button></div>}{view==='settings'&&<nav aria-label="Kategorie ustawień" className="flex min-w-0 flex-wrap items-center gap-1">
          {settingsTabs.map(tab=><Button key={tab.id} size="sm" variant={settingsTab===tab.id?'default':'ghost'} aria-pressed={settingsTab===tab.id} onClick={()=>setSettingsTab(tab.id)}>{tab.label}</Button>)}
        </nav>}</>}/></>}
      {view==='project'&&!current&&<Card><CardContent className="space-y-3 p-4"><p className="text-sm text-muted-foreground">{lastRefreshed?'Projekt nie jest dostępny.':'Wczytywanie projektu…'}</p><Button size="sm" variant="outline" onClick={()=>go('projects')}>Lista projektów</Button></CardContent></Card>}
      {view==='dashboard'&&<><div className="grid grid-cols-2 gap-3 xl:grid-cols-4"><Metric label="Projekty" value={String(projects.length)} foot="Domeny .test" icon={<FolderCode className="size-5"/>}/><Metric label="Aktywne usługi" value={serviceStatusUnavailable?'—':`${managed.filter(s=>s.state==='active').length}/${managed.length}`} foot="Uruchomione / wszystkie" icon={<Activity className="size-5"/>}/><Metric label="HTTPS" value={String(projects.filter(p=>p.https).length)} foot="Auto SSL" icon={<LockKeyhole className="size-5"/>}/><Metric label="Bazy danych" value={String(projects.filter(p=>p.database).length)} foot="MariaDB" icon={<Database className="size-5"/>}/></div>
        <div className="grid gap-3 xl:grid-cols-[1.1fr_0.9fr]"><ManagedServices services={managed} csrf={csrf} request={request} refresh={()=>refresh()} statusUnavailable={serviceStatusUnavailable} blocked={engineBusy||!!server.service_action_pending} onAutostartSaved={(id,enabled)=>{
          appliedServiceSequence.current=++serviceSequence.current
          setServer(prev=>({...prev,managed_services:prev.managed_services?.map(s=>s.id===id?{...s,autostart:enabled}:s)}))
        }}/>
        <Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle>Ostatnie projekty</CardTitle></div><Button size="sm" variant="outline" onClick={()=>go('projects')}>Wszystkie<ArrowRight/></Button></div></CardHeader><CardContent>{projects.length===0?<p className="py-8 text-center text-sm text-muted-foreground">Brak projektów.</p>:<div className="divide-y">{projects.slice(0,5).map(p=><button key={p.slug} className="flex w-full items-center gap-3 py-3 text-left hover:opacity-75" onClick={()=>details(p.slug)}><IconBox><FolderCode className="size-4"/></IconBox><div className="min-w-0 flex-1"><div className="break-words text-sm font-medium">{p.name}</div><span className="break-all text-xs text-muted-foreground">{p.domain}</span></div>{p.https&&<LockKeyhole className="size-4 text-emerald-600"/>}<ChevronRight className="size-4 text-muted-foreground"/></button>)}</div>}</CardContent></Card></div></>}
      {view==='projects'&&<Card>
        <CardHeader><div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2"><CardTitle>Lista projektów</CardTitle><span className="text-xs text-muted-foreground" aria-live="polite">{visibleProjects.length} / {projects.length}</span></div>
          <div className="relative w-full sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden="true"/>
            <Input type="search" aria-label="Szukaj projektów" placeholder="Nazwa, domena, framework…" className="pl-8 pr-9 [&::-webkit-search-cancel-button]:appearance-none" value={projectSearch} onChange={e=>setProjectSearch(e.target.value)}/>
            {projectSearch&&<button type="button" aria-label="Wyczyść wyszukiwanie projektów" className="absolute right-1.5 top-1.5 flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring" onClick={()=>setProjectSearch('')}><X className="size-4"/></button>}
          </div>
        </div></CardHeader>
        <CardContent>{projects.length===0?<p className="py-8 text-center text-sm text-muted-foreground">Jeszcze nie dodano projektów.</p>:visibleProjects.length===0?<p className="py-8 text-center text-sm text-muted-foreground">Brak projektów pasujących do wyszukiwania.</p>:<div className="divide-y">
          {visibleProjects.map(p=><button key={p.slug} className="flex w-full min-w-0 flex-wrap items-center gap-2 rounded-sm py-2 text-left hover:bg-secondary/40 focus-visible:outline-2 focus-visible:outline-ring" onClick={()=>details(p.slug)}>
            <IconBox><FolderCode className="size-5"/></IconBox>
            <div className="min-w-0 flex-1 basis-40"><div className="break-words font-medium">{p.name}</div><span className="break-all text-sm text-muted-foreground">{p.domain}</span></div>
            <div className="flex min-w-0 flex-wrap items-center gap-2"><FrameworkBadge project={p}/><Badge>{p.database?'MariaDB':p.engine==='laravel'?'SQLite':'Bez bazy'}</Badge>{p.https&&<HttpsBadge/>}<ChevronRight className="size-4 shrink-0 text-muted-foreground"/></div>
          </button>)}
        </div>}</CardContent>
      </Card>}
      {view==='new'&&<div className="grid gap-3 xl:grid-cols-[1.2fr_0.8fr]"><Card><CardHeader><CardTitle>Konfiguracja projektu</CardTitle></CardHeader><CardContent><form id="new-project-form" className="space-y-3" onSubmit={create}><div className="grid gap-3 sm:grid-cols-2"><div className="sm:col-span-2"><Field label="Nazwa projektu"><Input aria-label="Nazwa projektu" required maxLength={70} value={draft.name} onChange={e=>setDraft(d=>({...d,name:e.target.value}))}/></Field></div><Field label="Identyfikator katalogu" hint={draft.slug===null?'Domyślnie z nazwy projektu.':'Własny identyfikator katalogu.'}><Input aria-label="Identyfikator katalogu" value={identity.slug} required maxLength={40} pattern={'[a-z][a-z0-9\\-]{1,39}'} title="2–40 małych liter, cyfr lub myślników; zacznij od litery" onChange={e=>setDraft(d=>({...d,slug:e.target.value}))}/>{draft.slug!==null&&<button type="button" className="text-xs text-muted-foreground underline" onClick={()=>setDraft(d=>({...d,slug:null}))}>Przywróć automatyczny identyfikator</button>}</Field><Field label="Domena .test" hint={draft.domain===null?'Domyślnie z identyfikatora.':'Własna domena projektu.'}><Input aria-label="Domena .test" value={identity.domain} required maxLength={45} pattern={'[A-Za-z][A-Za-z0-9\\-]{0,39}\\.[Tt][Ee][Ss][Tt]'} title="Domena lokalna w formacie projekt.test" onChange={e=>setDraft(d=>({...d,domain:e.target.value}))}/>{draft.domain!==null&&<button type="button" className="text-xs text-muted-foreground underline" onClick={()=>setDraft(d=>({...d,domain:null}))}>Przywróć automatyczną domenę</button>}</Field></div><Field label="Framework" hint="Oficjalny instalator frameworka."><select aria-label="Framework" className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={draft.engine} onChange={e=>{const id=e.target.value;const definition=engines[id];setKitInfo(null);setInstallerChoices({});setDraft(d=>({...d,engine:id,starter_kit:definition?.default_option??'none'}))}}><option value="none">Bez frameworka — pusty projekt</option>{Object.entries(engines).map(([id,definition])=><option key={id} value={id} disabled={!engineAllowed}>{definition.label}</option>)}</select>{!engineAllowed&&<p className="text-xs text-muted-foreground">Instalatory wymagają logowania lub jednorazowej zgody administratora. Szczegóły w ustawieniach.</p>}</Field>{draftEngine&&<><Field label={draftEngine.option_label} hint={draftEngine.option_hint}><select required aria-label={draftEngine.option_label} disabled={kitLoading||!starterKits.length||engineBusy} className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={kitReady?draft.starter_kit:''} onChange={e=>{setInstallerChoices({});setDraft(d=>({...d,starter_kit:e.target.value}))}}><option value="" disabled>{kitLoading?'Odczytywanie starter kitów…':starterKits.length?'Wybierz starter kit':'Brak dostępnej listy'}</option>{starterKits.map(option=><option key={option.id} value={option.id}>{option.label}</option>)}</select><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted-foreground">{installedKitInfo?.installed?draftEngine.tool+' '+installedKitInfo.installed:''}</p><Button type="button" variant="ghost" size="sm" disabled={kitLoading||engineBusy||!engineAllowed} onClick={()=>{setKitInfo(null);setKitRefresh(v=>v+1)}}><RefreshCw/>Odśwież listę</Button></div></Field>{kitReady&&resolvedOptions.fields.length>0&&<div className="grid gap-3 rounded-xl border p-3 sm:grid-cols-2"><div className="text-sm font-medium sm:col-span-2">Opcje instalatora</div>{resolvedOptions.fields.map(field=><Field key={field.id} label={field.label}><select required aria-label={field.label} disabled={busy||engineBusy} className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" value={resolvedOptions.values[field.id]} onChange={e=>{const next={...resolvedOptions.values,[field.id]:e.target.value};setInstallerChoices(resolveInstallerFields(installerFields,draft.starter_kit,next).values)}}>{field.choices.map(choice=><option key={choice.id} value={choice.id}>{choice.label}</option>)}</select></Field>)}</div>}<p className="text-xs text-muted-foreground">Wymagane narzędzie: {draftEngine.tool}. <button type="button" className="underline" onClick={()=>{setSelectedTool(draft.engine);setSettingsTab('installers');go('settings')}}>Zarządzaj oficjalnym instalatorem w ustawieniach</button>.</p></>}{draftEngine?<Field label="Katalog publiczny" hint="Ustawiany automatycznie przez wybrany framework."><div className="flex h-9 items-center justify-between gap-2 rounded-md border bg-secondary/40 px-3 text-sm"><code>{effectiveWebroot}</code><Badge>Automatycznie</Badge></div></Field>:<Field label="Katalog publiczny" hint="Ścieżka względna projektu"><Input value={effectiveWebroot} required onChange={e=>setDraft(d=>({...d,webroot:e.target.value}))}/></Field>}<div className="space-y-3 rounded-xl border p-4">{draftEngine?.database_options?<Field label="Baza danych" hint={draftEngine.database_hint}><select aria-label="Baza danych" className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring" value={draft.database?'mariadb':'sqlite'} onChange={e=>setDraft(d=>({...d,database:e.target.value==='mariadb'}))}>{draftEngine.database_options.map(option=><option key={option.id} value={option.id}>{option.label}</option>)}</select></Field>:<div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium">Utwórz bazę MariaDB</div><p className="text-xs text-muted-foreground">Oddzielny użytkownik i hasło w panelu.</p></div><Switch aria-label="Utwórz bazę danych" checked={draft.database} onCheckedChange={v=>setDraft(d=>({...d,database:v}))}/></div>}<div className="border-t pt-4"><div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium">Auto SSL (HTTPS)</div><p className="text-xs text-muted-foreground">mkcert i przekierowanie HTTP → HTTPS.</p></div><Switch aria-label="Włącz Auto SSL" checked={draft.https} onCheckedChange={v=>setDraft(d=>({...d,https:v}))}/></div></div><div className="flex items-start justify-between gap-3 border-t pt-4"><div className="space-y-1"><div className="text-sm font-medium">Lokalna poczta testowa</div><p className="text-xs text-muted-foreground">{draft.engine==='laravel'?'SMTP w pliku .env i lokalne PHP mail().':'Lokalne PHP mail() w puli PHP-FPM.'} Wiadomości trafiają do wspólnej skrzynki Mailpit zamiast do prawdziwych adresatów.</p>{(serviceStatusUnavailable||server.mailpit!=='active')&&<button type="button" className="text-xs underline" onClick={()=>go('mail')}>Uruchom Mailpit w zakładce E-mail</button>}</div><input type="checkbox" className="mt-1 size-5 shrink-0 accent-primary" aria-label="Lokalna poczta testowa" checked={draft.local_mail} disabled={(serviceStatusUnavailable||server.mailpit!=='active')&&!draft.local_mail} onChange={e=>setDraft(d=>({...d,local_mail:e.target.checked}))}/></div></div><Button type="submit" className="w-full" disabled={busy||engineBusy||(draft.local_mail&&(serviceStatusUnavailable||server.mailpit!=='active'))||(!!draftEngine&&(!engineAllowed||!kitReady))}><Plus/>{busy||engineBusy?'Tworzenie…':'Utwórz projekt'}</Button></form></CardContent></Card><div className="space-y-3"><Card><CardHeader><CardTitle>Podgląd</CardTitle></CardHeader><CardContent className="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-1"><Info stacked label="Domena" value={`${draft.https?'https':'http'}://${identity.domain.trim().toLowerCase()||'projekt.test'}`}/><Info stacked label="Katalog" value={`~/projects/${identity.slug.trim()||'projekt'}`}/><Info stacked label="Webroot" value={effectiveWebroot}/><Info stacked label="Framework" value={draftEngine?draftEngine.label+' · '+(starterKits.find(o=>o.id===draft.starter_kit)?.label??'Wybierz starter kit'):'Bez frameworka'}/>{kitReady&&resolvedOptions.fields.map(field=><Info stacked key={field.id} label={field.label} value={field.choices.find(choice=>choice.id===resolvedOptions.values[field.id])?.label??''}/>)}<Info stacked label="Baza" value={draft.database?(draftEngine?.database_options?'MariaDB · dane w .env':'Nowa MariaDB'):(draftEngine?.database_options?'SQLite':'Bez bazy')}/><Info stacked label="Certyfikat" value={draft.https?'mkcert · Auto SSL':'Wyłączony'}/><Info stacked label="Poczta testowa" value={draft.local_mail?'Mailpit · 127.0.0.1:1025':'Bez automatycznej konfiguracji'}/></CardContent></Card><div className="rounded-xl border bg-secondary/40 p-3 text-sm text-muted-foreground"><ShieldCheck className="mr-2 inline size-4 text-foreground"/><strong className="text-foreground">Bezpieczne wykonanie</strong><p className="mt-2">Instalator działa w Ubuntu. Domena .test zostanie zsynchronizowana z Windows.</p></div></div></div>}
      {view==='project'&&current&&<div className="space-y-3"><div className="grid gap-3 xl:grid-cols-2"><Card><CardHeader><div className="flex items-center justify-between gap-3"><CardTitle>Projekt</CardTitle>{current.https&&<HttpsBadge/>}</div></CardHeader><CardContent className="space-y-3"><Info label="Domena" value={current.domain} action={<Button variant="ghost" size="icon" aria-label="Otwórz stronę" onClick={()=>window.open(`${current.https?'https':'http'}://${current.domain}`,'_blank','noopener,noreferrer')}><ExternalLink/></Button>}/><Info label="Folder" value={current.path}/><Info label="Webroot" value={current.webroot}/>{current.detected_framework&&(!current.engine||current.engine==='none')&&<Info label="Framework" value={projectFramework(current)}/>}{current.engine&&current.engine!=='none'&&<><Info label="Framework" value={(current.detected_framework?projectFramework(current):(engines[current.engine]?.label??current.engine)+' · wersja nieznana')+' · '+(current.starter_kit_label??(current.starter_kit==='none'?'Blade (bez starter kitu)':current.starter_kit==='livewire'?'Livewire':current.starter_kit??''))}/>{current.installer_option_labels?.map((option,i)=><Info key={i} label={option.label} value={option.value}/>)}{current.installer_options?.assets==='no'&&<p className="text-xs text-muted-foreground">Budowanie zasobów pominięto podczas instalacji. Przygotuj je przed otwarciem aplikacji, jeśli jeszcze nie zostały zbudowane.</p>}<Info label="Instalacja" value={current.engine_status==='ready'?'Środowisko gotowe':current.engine_status==='failed'?'Wymaga kontroli':'W toku'}/></>}<Info label="Auto SSL" value={current.https?'Włączone':'Wyłączone'}/><Info label="Poczta testowa" value={current.local_mail?'Mailpit · ustawiono przy tworzeniu':'Nie skonfigurowano przez DevStack'} action={<Button size="icon" variant="ghost" aria-label="Przejdź do skrzynki E-mail" onClick={()=>go('mail')}><Mail/></Button>}/></CardContent></Card><Card><CardHeader><CardTitle>{!current.database&&current.engine==='laravel'?'SQLite':'MariaDB'}</CardTitle><CardDescription>{current.engine==='laravel'?(current.database?'Dane połączenia zapisane w pliku .env.':'Lokalny plik bazy aplikacji Laravel.'):'Konto przypisane tylko do tej bazy'}</CardDescription></CardHeader><CardContent className="space-y-3">{current.database?<><Info label="Host" value="127.0.0.1" action={<Button variant="ghost" size="icon" aria-label="Kopiuj host" onClick={()=>{void copy('127.0.0.1')}}><Copy/></Button>}/><Info label="Port" value="3306"/><Info label="Baza" value={current.db_name} action={<Button variant="ghost" size="icon" aria-label="Kopiuj bazę" onClick={()=>{void copy(current.db_name)}}><Copy/></Button>}/><Info label="Użytkownik" value={current.db_user} action={<Button variant="ghost" size="icon" aria-label="Kopiuj użytkownika" onClick={()=>{void copy(current.db_user)}}><Copy/></Button>}/><form onSubmit={reveal} className="rounded-xl border bg-secondary/30 p-3"><Field label={passwordRequired?'Potwierdź hasło panelu, aby zobaczyć hasło bazy':'Pokaż hasło lokalnej bazy'}><div className="flex gap-2">{passwordRequired&&<Input type="password" autoComplete="off" aria-label="Hasło panelu" value={revealPassword} onChange={e=>setRevealPassword(e.target.value)} required/>}<Button type="submit" disabled={busy}>Pokaż</Button></div></Field>{dbSecret&&<div className="mt-3 flex items-center gap-2"><code className="min-w-0 flex-1 select-all break-all rounded-md bg-background p-3 text-xs">{dbSecret}</code><Button size="icon" variant="outline" aria-label="Kopiuj hasło" onClick={()=>{void copy(dbSecret)}}><Copy/></Button><Button size="icon" variant="outline" aria-label="Ukryj hasło" onClick={()=>setDbSecret('')}><X/></Button></div>}<p className="mt-2 text-xs text-muted-foreground">Hasło znika po 20 sekundach. W trybie bez logowania lokalny dostęp do panelu wystarcza do jego ujawnienia.</p></form><Button variant="outline" className="w-full" disabled={busy||testingDb} onClick={()=>{void testDatabase()}}><Database/>{testingDb?'Testowanie MariaDB…':'Test połączenia MariaDB'}</Button></>:<p className="text-sm text-muted-foreground">{current.engine==='laravel'?'SQLite: database/database.sqlite w katalogu projektu.':'Ten projekt nie ma zarządzanej bazy MariaDB.'}</p>}</CardContent></Card></div>
        <Card><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Konfiguracja Nginx</CardTitle></div><Badge><FileCode2 className="size-3"/>Zarządzana</Badge></div></CardHeader><CardContent className="space-y-3">
          <Field label="Dodatkowe reguły Nginx" hint="Dyrektywy limitów i timeoutów oraz dozwolone bloki location dla zasobów aplikacji.">
            <Textarea aria-label="Dodatkowe reguły Nginx" className="min-h-36 font-mono text-xs" spellCheck={false} disabled={busy||fullEditing} value={rules} onChange={e=>setRulesDraft({slug:current.slug,value:e.target.value})} rows={8}/>
            <details className="text-xs text-muted-foreground"><summary className="cursor-pointer font-medium text-foreground">Obsługiwane reguły i przykład</summary><p className="mt-2">client_max_body_size (1–999m), client_body_timeout i send_timeout (1–999s). W blokach location obsługiwane są konkretne ścieżki JS/CSS, opcjonalne expires off oraz try_files. DevStack umieszcza reguły we właściwych sekcjach.</p><pre className="mt-2 overflow-x-auto rounded-md border bg-secondary/40 p-2 font-mono">{'client_max_body_size 64m;\n\nlocation ~* ^/flux/(flux|editor)(\\.min)?\\.(js|css)$ {\n    expires off;\n    try_files $uri $uri/ /index.php?$query_string;\n}'}</pre></details>
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">Kopia → nginx -t → reload albo cofnięcie.</p><Button disabled={busy || fullEditing} onClick={()=>{void saveConfig()}}><Check/>{busy?'Zapisywanie…':'Sprawdź i zastosuj'}</Button></div>
          <div className="border-t pt-4">
            <Button variant="outline" className="w-full justify-between" onClick={()=>setFullOpen(v=>!v)} aria-expanded={fullOpen}>
              <span className="flex items-center gap-2"><FileCode2 className="size-4"/> {fullOpen?'Ukryj pełną konfigurację':'Pokaż pełną konfigurację'}</span>
              {fullOpen?<ChevronUp className="size-4"/>:<ChevronDown className="size-4"/>}
            </Button>
            {fullOpen&&<div className="mt-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">Pełny plik Nginx</h3><p className="text-xs text-muted-foreground">Edycja zarządzanych sekcji.</p></div><Button variant="outline" size="sm" disabled={busy} onClick={toggleFullEdit}><Pencil className="size-4"/>{fullEditing?'Anuluj edycję':'Edytuj'}</Button></div>
              <Textarea aria-label="Pełna konfiguracja Nginx" className="font-mono text-xs leading-relaxed" rows={18} spellCheck={false} value={fullEditing?fullConfig:current.nginx_config} readOnly={!fullEditing} onChange={e=>setFullConfig(e.target.value)}/>
              {fullEditing&&<div className="space-y-3 rounded-lg border p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">Edycja tylko między znacznikami DevStack. Domena, SSL, root i PHP-FPM pozostają chronione. Zapis aktualizuje również dodatkowe reguły Nginx.</p>
                {passwordRequired&&<Field label="Potwierdź hasło administratora"><Input type="password" autoComplete="off" value={fullPassword} onChange={e=>setFullPassword(e.target.value)} required/></Field>}
                <div className="flex justify-end"><Button onClick={()=>{void saveFullConfig()}} disabled={busy||(passwordRequired&&!fullPassword)}><Check/>{busy?'Sprawdzanie…':'Sprawdź i zapisz zmiany'}</Button></div>
              </div>}
            </div>}
          </div>
        </CardContent></Card>
        <Card className="border-destructive/35"><CardHeader><CardTitle className="text-destructive">Usuwanie projektu</CardTitle><CardDescription>Usuwa konfigurację Nginx, pulę PHP-FPM i certyfikat projektu. Pliki w katalogu projektu pozostają na dysku.</CardDescription></CardHeader><CardContent className="space-y-3">
          {!deleteOpen?<Button variant="destructive" disabled={busy} onClick={()=>{setDeleteConfirmation('');setDeleteDbConfirm(false);setDeleteOpen(true)}}><Trash2/>Usuń projekt z DevStack</Button>:<form className="space-y-3" onSubmit={deleteProject}>
            <p className="text-sm">Potwierdź usunięcie projektu <strong>{current.name}</strong> (<code>{current.slug}</code>). Nie dotyczy to plików źródłowych w <code>{current.path}</code>.</p>
            {current.database&&<label className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"><input type="checkbox" className="mt-1 size-4 accent-red-600" checked={deleteDbConfirm} onChange={e=>setDeleteDbConfirm(e.target.checked)}/><span><strong>Rozumiem, że baza MariaDB {current.db_name} i jej użytkownik zostaną trwale usunięci.</strong> Dane bazy nie mogą zostać odzyskane bez kopii zapasowej.</span></label>}
            <Field label={`Wpisz dokładnie: ${current.slug}`}><Input autoComplete="off" spellCheck={false} value={deleteConfirmation} onChange={e=>setDeleteConfirmation(e.target.value)} required/></Field>
            <div className="flex flex-wrap gap-2"><Button type="submit" variant="destructive" disabled={busy||deleteConfirmation!==current.slug||(current.database&&!deleteDbConfirm)}><Trash2/>{busy?'Usuwanie…':'Potwierdź usunięcie'}</Button><Button variant="outline" type="button" disabled={busy} onClick={()=>{setDeleteOpen(false);setDeleteConfirmation('');setDeleteDbConfirm(false)}}>Anuluj</Button></div>
          </form>}
        </CardContent></Card></div>}
      {view==='mail'&&<MailInbox key={mailRoute} initialMessage={mailRoute} status={serviceStatusUnavailable?'unknown':server.mailpit} csrf={csrf} request={request} refreshStatus={()=>{void refresh()}}/>}
      {view==='settings'&&<div className="space-y-3">
        
        <section aria-label="Środowisko" hidden={settingsTab!=='environment'}><Card><CardHeader><CardTitle>Parametry środowiska</CardTitle></CardHeader><CardContent className="space-y-2"><Info label="Panel" value="https://devstack.test"/><Info label="Backend" value="PHP-FPM 8.5 + Unix socket"/><Info label="SSL" value="mkcert (prywatny lokalny CA)"/><Info label="Usługi" value="systemd"/></CardContent></Card><div className="mt-3"><Card><CardHeader><CardTitle>Motyw</CardTitle></CardHeader><CardContent className="flex items-center justify-between gap-3"><span className="text-sm">{theme==='dark'?'Ciemny':'Jasny'}</span><Button size="sm" variant="outline" disabled={savingTheme} onClick={()=>{void toggleTheme()}}>{theme==='dark'?<Sun/>:<Moon/>}Zmień motyw</Button></CardContent></Card></div></section>
        <section aria-label="Instalatory" hidden={settingsTab!=='installers'}><Card><CardHeader><CardTitle>Narzędzia instalacyjne</CardTitle><CardDescription>Instalatory frameworków w Ubuntu.</CardDescription></CardHeader><CardContent className="space-y-3">{Object.keys(engines).length>1&&<select aria-label="Instalator" className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm" value={toolId} onChange={e=>setSelectedTool(e.target.value)}>{Object.entries(engines).map(([id,definition])=><option key={id} value={id}>{definition.tool}</option>)}</select>}<Info label="Narzędzie" value={toolDefinition?.tool??'Brak'}/>{toolDefinition&&<p className="text-xs text-muted-foreground">{toolDefinition.description}</p>}{!engineAllowed?<div className="space-y-2 text-xs text-muted-foreground"><p>Zgoda administratora na instalatory w trybie bez hasła:</p><code className="block break-all rounded bg-secondary p-2">sudo php /opt/devstack/scripts/configure-auth.php --allow-local-installers --i-understand-local-risk</code><p>Odśwież panel po wykonaniu polecenia.</p></div>:<><Info label="Zainstalowana" value={toolLoading?'Odczytywanie…':toolInfo?.installed??(toolInfo?'Brak':'Nieznana')}/><Info label="Dostępna" value={engineJob&&engineJob.engine===toolId&&engineJob.action==='check'&&engineJob.state==='succeeded'?engineJob.tool?.latest??'Nieznana':'Sprawdź aktualizacje'}/>{toolInfo?.composer===false&&<p className="text-xs text-muted-foreground">Composer nie jest dostępny. Zainstaluj go w Ubuntu.</p>}<div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={busy||engineBusy||toolLoading||!toolInfo?.installed} onClick={()=>void toolAction('check')}><RefreshCw/>Sprawdź aktualizacje</Button><Button variant="outline" size="sm" disabled={busy||engineBusy||toolLoading||!toolInfo?.composer} onClick={()=>void toolAction(toolInfo?.installed?'update-tool':'install-tool')}>{toolInfo?.installed?'Aktualizuj instalator':'Zainstaluj instalator'}</Button></div><p className="text-xs text-muted-foreground">Aktualizacja instalatora nie zmienia istniejących projektów.</p></>}</CardContent></Card></section>
        <section aria-label="Windows" hidden={settingsTab!=='windows'}><Card><CardHeader><CardTitle>Synchronizacja Windows</CardTitle></CardHeader><CardContent className="space-y-3 text-sm"><p>Zmiany domen .test są automatycznie zapisywane w zarządzanym bloku Windows hosts.</p><p className="text-muted-foreground">Pierwsza instalacja wymaga administratora Windows. Ręczna synchronizacja: <code className="break-all rounded bg-secondary px-1">windows/sync-hosts.ps1</code>.</p></CardContent></Card></section>
        <section aria-label="Widżety" hidden={settingsTab!=='widgets'}><WidgetServiceSettings services={managed} csrf={csrf} request={request} refresh={()=>refresh()}/></section>
        <section aria-label="Powiadomienia" hidden={settingsTab!=='notifications'}><NotificationSettings model={notifications}/></section>
        <section aria-label="Bezpieczeństwo" hidden={settingsTab!=='security'}><div className="space-y-3"><Card><CardHeader><CardTitle>Dostęp do panelu</CardTitle></CardHeader><CardContent className="space-y-2"><Info label="Logowanie" value={passwordRequired?'Włączone (hasło)':'Wyłączone (tylko lokalnie)'}/><Info label="Instalatory" value={engineAllowed?(passwordRequired?'Po zalogowaniu':'Dozwolone lokalnie bez hasła'):'Wymagają zgody administratora'}/><p className="text-xs text-muted-foreground">Zmiana trybu logowania (administrator Ubuntu): <code className="break-all">sudo php /opt/devstack/scripts/configure-auth.php --setup</code></p></CardContent></Card><Card><CardHeader><CardTitle>Certyfikat CA w Windows</CardTitle><CardDescription>Publiczny certyfikat mkcert dla Windows.</CardDescription></CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">Import CA wymaga ręcznego potwierdzenia w Windows.</p>
          <Button type="button" variant="outline" onClick={()=>{window.location.assign('/api/ca')}}><ShieldCheck/>Pobierz publiczny certyfikat CA (.cer)</Button>
          <p className="break-all text-xs text-muted-foreground">SHA-256 pobieranego pliku PEM: <code className="font-mono">{caFingerprint||'Odczytywanie…'}</code>{caInfoError?' (Brak dostępu do certyfikatu — sprawdź usługę DevStack.)':''}</p>
          <ol className="list-decimal space-y-1.5 pl-5 text-sm text-muted-foreground">
            <li>Zapisz pobrany <code>devstack-rootCA.cer</code>. W Windows PowerShell sprawdź hash: <code>Get-FileHash -Algorithm SHA256 -LiteralPath "$env:USERPROFILE\\Downloads\\devstack-rootCA.cer"</code>.</li>
            <li>Porównaj hash z wartością powyżej i — przy pierwszej instalacji — także z wynikiem odczytu certyfikatu bezpośrednio z zaufanego Ubuntu: <code>wsl -d Ubuntu-26.04 -u root -- sha256sum /var/lib/devstack/ca/rootCA.pem</code>.</li>
            <li>Uruchom w Windows <code>certmgr.msc</code>, przejdź do <strong>Zaufane główne urzędy certyfikacji → Certyfikaty → Importuj</strong> i wybierz plik .cer. Potwierdź świadomie dodanie zaufanego CA tylko dla Twojego DevStack.</li>
            <li>Uruchom ponownie przeglądarkę lub odśwież stronę po imporcie.</li>
          </ol>
          <p className="text-xs text-muted-foreground">Gdy przeglądarka blokuje otwarcie strony z powodu brakującego CA, użyj wstępnego skryptu <code>windows/setup-windows.ps1</code> zgodnie z instrukcją instalacji. DevStack nie pobiera i nigdy nie udostępnia prywatnego klucza CA.</p>
        </CardContent></Card></div></section>
      </div>}
      <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs text-muted-foreground"><span>DevStack · 0.1.0</span><span>PHP 8.5 · localhost</span></footer>
      </main></div>
  </div>
}
