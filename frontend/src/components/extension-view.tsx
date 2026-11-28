import { useEffect, useState } from 'react'
import { BookOpen, Copy, ExternalLink, Package, RefreshCw, Wrench } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { request, runOperation } from '@/lib/api'

const icons = { wrench: Wrench, package: Package, book: BookOpen }
type Icon = keyof typeof icons
export type ExtensionNav = { id: string; menu: { label: string; icon: Icon; order: number }; title: string }
type Section = { id: string; label: string; kind: 'overview' | 'instructions' | 'projects' | 'automations' }
type Tool = {
  id: string; name: string; description: string; version: string; installed: boolean; ready: boolean
  dependencies: string[]; documentation: string; example: string; service_state: string | null
}
type Instruction = {
  id: string; name: string; description: string; format: string; version: string; tools: string[]
  requires: string[]; reason: string; project_count: number; project_slugs: string[]
}
type ProjectRow = {
  id: string; slug: string; name: string; technologies: string[]; services: string[]; automatic: boolean
  status: string; last_sync: string | null; wanted: Record<string, string>; results: Record<string, string>
  manual: string[]; conflicts: string[]; migratable: boolean
}
type Operation = 'sync' | 'set_automatic' | 'migrate'
type Automation = {
  id: string; label: string; description: string; events: string[]
  last_run: { event: string; at: string; projects: number; errors: number } | null
}
type ActiveExtension = {
  state: 'active'; id: string; name: string; description: string; version: string; build: string
  view: { title: string; sections: Section[] }
  tools: Tool[]; instructions: Instruction[]; automations: Automation[]; operations: Operation[]
  terminal: { id: string; label: string; command: string }[]
  automatic: boolean; worker_error: boolean; lifecycle: { changed_at?: string; event?: string }
}
type Extension = ActiveExtension | { state: 'absent' | 'legacy' | 'busy' | 'invalid' | 'incompatible' }
type OperationData = { action: Operation; project?: string; enabled?: boolean }
type Summary = { id: string; title: string; status: string; count: number }
type ProjectPage = { projects: ProjectRow[]; next: number | null }

const statuses: Record<string, string> = {
  pending: 'Oczekuje', synced: 'Zsynchronizowano', disabled: 'Wyłączone',
  manual: 'Podłączono ręcznie', conflict: 'Konflikt', error: 'Błąd synchronizacji',
  unsafe_directory: 'Nie można bezpiecznie otworzyć katalogu', directory_changed: 'Katalog projektu został zmieniony',
  missing: 'Brak katalogu projektu', linked: 'Zarządzane', detached: 'Odłączone',
}
const events: Record<string, string> = {
  startup: 'Uruchomienie DevStack', project_created: 'Rejestracja projektu', framework_ready: 'Gotowy framework',
  project_changed: 'Zmiana konfiguracji projektu', project_removed: 'Usunięcie projektu',
  extension_installed: 'Instalacja rozszerzenia', extension_updated: 'Aktualizacja rozszerzenia',
  extension_rolled_back: 'Przywrócenie wydania', extension_removed: 'Usunięcie rozszerzenia', manual: 'Synchronizacja ręczna',
}

export function ExtensionIcon({ name }: { name: Icon }) {
  const Icon = icons[name] ?? Package
  return <Icon className="size-4" aria-hidden="true" />
}

function timeLabel(value: string) {
  return new Date(value).toLocaleString('pl-PL')
}

function Overview({ extension, copy }: { extension: ActiveExtension; copy: (text: string) => void }) {
  return <>
    <Card>
      <CardHeader><CardTitle>Aktywne wydanie <Badge>{extension.version}</Badge></CardTitle>
        <CardDescription>{extension.description}</CardDescription></CardHeader>
      <CardContent className="space-y-3">
        <p className="break-all text-sm text-muted-foreground">BUILD <code>{extension.build}</code></p>
        <div className="flex flex-wrap gap-2">
          {extension.terminal.map(item => <Button key={item.id} variant="outline" size="sm" onClick={() => copy(item.command)}>
            <Copy />{item.label}
          </Button>)}
        </div>
      </CardContent>
    </Card>
    <div className="grid gap-3 xl:grid-cols-3">
      {extension.tools.map(tool => <Card key={tool.id}>
        <CardHeader><CardTitle>{tool.name} <Badge>{tool.version}</Badge></CardTitle><CardDescription>{tool.description}</CardDescription></CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-2"><Badge>{tool.installed ? 'Zainstalowano' : 'Brak plików'}</Badge>
            <Badge>{tool.ready ? 'Gotowe' : tool.service_state === 'not-installed' ? 'Brak obsługiwanej usługi' : 'Niegotowe'}</Badge></div>
          <p className="text-muted-foreground">{tool.dependencies.join(' · ')}</p>
          <code className="block break-all rounded-md bg-secondary p-2">{tool.example}</code>
          <div className="flex flex-wrap items-center gap-3">
            <a className="inline-flex items-center gap-1 rounded-sm underline focus-visible:outline-2 focus-visible:outline-ring" href={tool.documentation} target="_blank" rel="noreferrer">Dokumentacja <ExternalLink className="size-3" /></a>
            <Button variant="ghost" size="sm" onClick={() => copy(tool.example)}><Copy />Kopiuj przykład</Button>
          </div>
        </CardContent>
      </Card>)}
    </div>
  </>
}

function InstructionPreview({ url, build }: { url: string; build: string }) {
  const [content, setContent] = useState('')
  const [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void request<{ text: string; build: string }>('GET', url, undefined, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted && value.build === build) setContent(value.text) })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [url, build])
  return <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-secondary p-3 text-xs" tabIndex={0}>
    {error ? 'Nie udało się pobrać treści instrukcji.' : content || 'Wczytywanie instrukcji…'}
  </pre>
}

function Instructions({ extension, url }: { extension: ActiveExtension; url: string }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  return <>
    {extension.instructions.length === 0 && <p>To wydanie nie dostarcza instrukcji.</p>}
    {extension.instructions.map(item => {
      const open = expanded === item.id
      return <Card key={item.id}>
        <CardHeader><CardTitle>{item.name} <Badge>{item.version}</Badge></CardTitle><CardDescription>{item.description}</CardDescription></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>{item.reason}</p>
          <p className="text-muted-foreground">Agent Skills · {item.tools.map(id => extension.tools.find(tool => tool.id === id)?.name ?? id).join(' · ')}</p>
          <p className="break-words">Projekty ({item.project_count}) {item.project_slugs.join(', ') || '· brak powiązań'}{item.project_count > item.project_slugs.length ? ' …' : ''}</p>
          <Button variant="outline" size="sm" aria-expanded={open} onClick={() => setExpanded(open ? null : item.id)}>
            {open ? 'Zwiń podgląd' : 'Podgląd instrukcji'}
          </Button>
          {open && <InstructionPreview key={extension.build} url={url + '/instructions/' + item.id} build={extension.build} />}
        </CardContent>
      </Card>
    })}
  </>
}

function Projects({ extension, page, offset, setOffset, pending, send }: {
  extension: ActiveExtension; page?: ProjectPage; offset: number; setOffset: (offset: number) => void
  pending: boolean; send: (data: OperationData) => void
}) {
  return <>
    {extension.operations.includes('sync') && <div><Button variant="outline" disabled={pending} onClick={() => send({ action: 'sync' })}><RefreshCw />Synchronizuj wszystkie projekty</Button></div>}
    {!extension.automatic && <p className="text-sm text-muted-foreground">
      Zarządzanie dla rozszerzenia jest wyłączone. Ustawienia projektów będą stosowane po jego włączeniu.
    </p>}
    {!page && <p role="status">Wczytywanie projektów…</p>}
    {page?.projects.length === 0 && <p className="text-sm text-muted-foreground">Brak projektów na tej stronie.</p>}
    {page?.projects.map(project => <Card key={project.id}>
      <CardHeader>
        <CardTitle><a className="rounded-sm underline focus-visible:outline-2 focus-visible:outline-ring" href={'#/project/' + project.slug}>{project.name}</a></CardTitle>
        <CardDescription>{[...project.technologies, ...project.services].join(' · ')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{statuses[project.status] ?? project.status}</Badge>
          {project.last_sync && <time dateTime={project.last_sync} className="text-muted-foreground">{timeLabel(project.last_sync)}</time>}
        </div>
        <ul className="space-y-1">
          {Object.entries(project.wanted).map(([name, reason]) => <li key={name}>
            <span className="font-medium">{extension.instructions.find(item => item.id === name)?.name ?? name}</span>
            {' · '}{reason}{project.results[name] && ' · ' + statuses[project.results[name]]}
          </li>)}
          {Object.keys(project.wanted).length === 0 && <li className="text-muted-foreground">Brak dobranych instrukcji.</li>}
        </ul>
        {project.manual.length > 0 && <p>Powiązania ręczne · {project.manual.join(', ')}</p>}
        {project.conflicts.length > 0 && <p>Konflikty · {project.conflicts.join(', ')}</p>}
        <div className="flex flex-wrap items-center gap-3">
          {extension.operations.includes('set_automatic') && <label className="flex items-center gap-2">
            <Switch disabled={pending} checked={project.automatic} aria-label={'Automatyczne instrukcje w projekcie ' + project.name}
              onCheckedChange={enabled => send({ action: 'set_automatic', enabled, project: project.id })} />Automatycznie
          </label>}
          {extension.operations.includes('sync') && <Button variant="outline" size="sm" disabled={pending}
            onClick={() => send({ action: 'sync', project: project.id })}>Ponów synchronizację</Button>}
          {extension.operations.includes('migrate') && project.migratable && project.automatic && extension.automatic &&
            <Button variant="outline" size="sm" disabled={pending} onClick={() => {
              if (window.confirm('Zastąpić ręczne dowiązanie do zbiorczego skilla automatycznie dobranymi instrukcjami?')) {
                send({ action: 'migrate', project: project.id })
              }
            }}>Migruj ręczne dowiązanie</Button>}
        </div>
      </CardContent>
    </Card>)}
    {(offset > 0 || page?.next != null) && <nav aria-label="Strony projektów" className="flex gap-2">
      <Button variant="outline" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 16))}>Poprzednia</Button>
      <Button variant="outline" disabled={page?.next == null} onClick={() => { if (page?.next != null) setOffset(page.next) }}>Następna</Button>
    </nav>}
  </>
}

function Automations({ extension, pending, send }: { extension: ActiveExtension; pending: boolean; send: (data: OperationData) => void }) {
  return <>
    <Card><CardHeader><CardTitle>Zarządzanie instrukcjami</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        {extension.operations.includes('set_automatic') && <label className="flex items-center gap-2">
          <Switch aria-label="Automatyczne zarządzanie instrukcjami" checked={extension.automatic} disabled={pending}
            onCheckedChange={enabled => send({ action: 'set_automatic', enabled })} />Automatyczne zarządzanie
        </label>}
        {extension.worker_error && <p role="status">Ostatnia synchronizacja zgłosiła błąd. Szczegóły są dostępne przy projektach.</p>}
        {extension.lifecycle.changed_at && <p>Ostatnia zmiana wydania · {events[extension.lifecycle.event ?? ''] ?? ''} · {timeLabel(extension.lifecycle.changed_at)}</p>}
        {extension.operations.includes('sync') && <Button variant="outline" disabled={pending} onClick={() => send({ action: 'sync' })}><RefreshCw />Ponów dobór i sprzątanie</Button>}
      </CardContent>
    </Card>
    {extension.automations.map(item => <Card key={item.id}><CardHeader>
      <CardTitle>{item.label} <Badge>{extension.automatic || item.events.some(event => event === 'project_removed' || event === 'extension_removed') ? 'Włączona' : 'Wyłączona'}</Badge></CardTitle>
      <CardDescription>{item.description}</CardDescription>
    </CardHeader><CardContent className="space-y-1 text-sm text-muted-foreground">
      <p>{item.events.map(event => events[event] ?? event).join(' · ')}</p>
      <p>{item.last_run
        ? `${timeLabel(item.last_run.at)} · ${events[item.last_run.event]} · Projekty ${item.last_run.projects} · Błędy ${item.last_run.errors}`
        : 'Brak odnotowanych przebiegów.'}</p>
    </CardContent></Card>)}
  </>
}

export function ExtensionView({ id, csrf, revision, copy }: { id: string; csrf: string; revision: number; copy: (text: string) => void }) {
  const [extension, setExtension] = useState<Extension>()
  const [selected, setSelected] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<ProjectPage>()
  const [error, setError] = useState(false)
  const [projectError, setProjectError] = useState(false)
  const [pending, setPending] = useState(false)
  const [reload, setReload] = useState(0)
  const url = '/api/extensions/' + id
  const active = extension?.state === 'active' ? extension : undefined
  const section = active?.view.sections.find(item => item.id === selected) ?? active?.view.sections[0]

  useEffect(() => {
    const controller = new AbortController()
    const refresh = async () => {
      try {
        const value = await request<Extension>('GET', url, undefined, undefined, controller.signal)
        if (!controller.signal.aborted) { setExtension(value); setError(false) }
      } catch {
        if (!controller.signal.aborted) setError(true)
      }
    }
    void refresh()
    const timer = window.setInterval(() => { if (!document.hidden) void refresh() }, 5000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [url, revision, reload])

  useEffect(() => {
    if (section?.kind !== 'projects') return
    const controller = new AbortController()
    const refresh = async () => {
      try {
        const value = await request<ProjectPage>('GET', url + '/projects?offset=' + offset, undefined, undefined, controller.signal)
        if (!controller.signal.aborted) { setPage(value); setProjectError(false) }
      } catch {
        if (!controller.signal.aborted) setProjectError(true)
      }
    }
    void refresh()
    const timer = window.setInterval(() => { if (!document.hidden) void refresh() }, 5000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [url, section?.kind, offset, revision, reload])

  function changePage(next: number) {
    setPage(undefined)
    setOffset(next)
  }

  async function send(data: OperationData) {
    setPending(true)
    const result = await runOperation('/api/extensions/instructions', csrf, { extension: id, ...data }, {
      title: 'Synchronizacja instrukcji',
      onResult: () => ({ kind: 'info', title: 'Przyjęto synchronizację. Wynik pojawi się przy projektach.' }),
    })
    setPending(false)
    if (result) setReload(value => value + 1)
  }

  if (!active || !section || error) return <>
    <PageHeader title="Rozszerzenie" />
    <Card><CardContent className="space-y-3 p-4 text-sm">
      <p role="status">{error ? 'Nie udało się odczytać stanu rozszerzenia.' :
        extension?.state === 'invalid' ? 'Manifest rozszerzenia jest nieprawidłowy.' :
        extension?.state === 'incompatible' ? 'Wydanie wymaga innej wersji DevStack Core.' :
        extension?.state === 'legacy' ? 'Zainstalowane wydanie nie udostępnia jeszcze widoku.' :
        extension?.state === 'absent' ? 'Rozszerzenie nie jest zainstalowane.' :
        extension?.state === 'busy' ? 'Trwa aktywacja wydania rozszerzenia.' : 'Wczytywanie rozszerzenia…'}</p>
      <Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}><RefreshCw />Odśwież</Button>
    </CardContent></Card>
  </>
  return <>
    <PageHeader title={active.view.title} actions={
      <nav aria-label="Sekcje rozszerzenia" className="flex flex-wrap gap-1">
        {active.view.sections.map(item => <Button key={item.id} size="sm" aria-pressed={section.id === item.id}
          variant={section.id === item.id ? 'default' : 'ghost'} onClick={() => setSelected(item.id)}>{item.label}</Button>)}
      </nav>
    } />
    <section className="space-y-3" aria-label={section.label}>
      {section.kind === 'overview' && <Overview extension={active} copy={copy} />}
      {section.kind === 'instructions' && <Instructions extension={active} url={url} />}
      {section.kind === 'projects' && (projectError ? <p role="alert">Nie udało się wczytać projektów.</p> :
        <Projects extension={active} page={page} offset={offset} setOffset={changePage} pending={pending} send={send} />)}
      {section.kind === 'automations' && <Automations extension={active} pending={pending} send={send} />}
    </section>
  </>
}

export function ProjectInstructionSummary({ slug, revision }: { slug: string; revision: number }) {
  const [summary, setSummary] = useState<Summary[]>([])
  useEffect(() => {
    const controller = new AbortController()
    void request<Summary[]>('GET', '/api/extensions/project-summary/' + slug, undefined, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setSummary(value) })
      .catch(() => { if (!controller.signal.aborted) setSummary([]) })
    return () => controller.abort()
  }, [slug, revision])
  if (!summary.length) return null
  return <Card><CardHeader><CardTitle>Zarządzane instrukcje</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {summary.map(item => <div key={item.id} className="flex flex-wrap items-center gap-2">
        <a href={'#/extensions/' + item.id} className="rounded-sm underline focus-visible:outline-2 focus-visible:outline-ring">{item.title}</a>
        <Badge>{statuses[item.status] ?? item.status}</Badge><span>{item.count} powiązań</span>
      </div>)}
    </CardContent>
  </Card>
}
