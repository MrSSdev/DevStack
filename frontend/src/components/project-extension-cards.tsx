import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown, CircleDashed, Clock3, Link, Pause, TriangleAlert, X } from 'lucide-react'
import { ExtensionIcon, type ExtensionIconName } from '@/components/extension-view'
import {
  InstructionPreview, ProjectInstructionActions, instructionStatuses, shortcutStyle, timeLabel,
  useInstructionOperations, type Operation, type ProjectInstructionState,
} from '@/components/extension-instructions'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { request } from '@/lib/api'
import { cn } from '@/lib/utils'

type Slot = 'project-details' | 'project-mail'
type Section = { id: string; label: string; kind: 'instructions'; operations: Operation[] }
type CardDefinition = {
  id: string; title: string; icon: ExtensionIconName; slot: Slot; order: number; sections: Section[]
}
type ProjectExtension = {
  id: string; build: string; cards: CardDefinition[]; automatic: boolean
  instructions: { id: string; name: string }[]; project: ProjectInstructionState
}

const emptyExtensions: ProjectExtension[] = []

export function useProjectExtensions(slug: string | undefined, revision: number) {
  const [snapshot, setSnapshot] = useState<{ slug: string; extensions: ProjectExtension[] }>()
  const [reload, setReload] = useState(0)
  const refresh = useCallback(() => setReload(value => value + 1), [])

  useEffect(() => {
    if (!slug) return
    const controller = new AbortController()
    const projectSlug = slug
    let timer: number
    async function read() {
      try {
        if (!document.hidden) {
          const value = await request<{ extensions: ProjectExtension[] }>(
            'GET', '/api/extensions/project-cards/' + projectSlug, undefined, undefined, controller.signal,
          )
          if (!controller.signal.aborted) setSnapshot({ slug: projectSlug, extensions: value.extensions })
        }
      } catch {
        if (!controller.signal.aborted) setSnapshot(undefined)
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(read, 2000)
      }
    }
    void read()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [slug, revision, reload])
  return { extensions: snapshot?.slug === slug ? snapshot?.extensions ?? emptyExtensions : emptyExtensions, refresh }
}

function InstructionDialog({ extension, card, instruction, close }: {
  extension: ProjectExtension; card: CardDefinition; instruction: { id: string; name: string }; close: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const id = useId()
  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
  }, [])
  const query = new URLSearchParams({ project: extension.project.id, card: card.id })
  const url = '/api/extensions/' + extension.id + '/instructions/' + instruction.id + '?' + query
  return <dialog ref={ref} aria-labelledby={id} onClose={close}
    className="m-auto w-[calc(100%-1.5rem)] max-w-2xl rounded-xl border bg-card p-3 text-card-foreground shadow-lg backdrop:bg-black/50">
    <div className="flex items-center justify-between gap-2">
      <h2 id={id} className="min-w-0 text-sm font-semibold wrap-anywhere">{instruction.name}</h2>
      <Button type="button" size="icon" variant="ghost" className={shortcutStyle} aria-label="Zamknij podgląd"
        onClick={() => ref.current?.close()}><X /></Button>
    </div>
    <InstructionPreview key={extension.build} url={url} build={extension.build} />
  </dialog>
}

const statusIcons = {
  linked: Check, manual: Link, pending: Clock3, selected: CircleDashed, detached: Pause,
  disabled: Pause, conflict: TriangleAlert, error: TriangleAlert,
}

function instructionStatus(name: string, project: ProjectInstructionState, automatic: boolean) {
  const result = project.results[name]
  if (project.conflicts.includes(name)) return 'conflict'
  if (project.manual.includes(name)) return 'manual'
  if (result === 'linked' || result === 'error') return result
  if (!automatic || !project.automatic) return 'disabled'
  return project.status === 'pending' ? 'pending' : 'selected'
}

function ProjectInstructionSection({ extension, card, section, sending }: {
  extension: ProjectExtension; card: CardDefinition; section: Section; sending: boolean
}) {
  const [preview, setPreview] = useState<string>()
  const [expanded, setExpanded] = useState(false)
  const detail = useId()
  const project = extension.project
  const rows = extension.instructions.filter(item => item.id in project.wanted ||
    project.manual.includes(item.id) || ['linked', 'conflict', 'error'].includes(project.results[item.id]))
  const selected = rows.find(item => item.id === preview)
  const error = ['error', 'unsafe_directory', 'directory_changed', 'missing'].includes(project.status)
  return <>
    <div className="flex min-w-0 flex-wrap items-center gap-x-2">
      <ul aria-label={section.label} className="flex min-w-0 flex-wrap items-center gap-x-1 text-xs">
        {rows.map(item => {
          const state = instructionStatus(item.id, project, extension.automatic)
          const Icon = statusIcons[state]
          const label = instructionStatuses[state]
          return <li key={item.id} className="min-w-0">
            <button type="button" title={label} onClick={() => setPreview(item.id)}
              className="inline-flex min-h-7 max-w-full items-center gap-1 rounded-md px-1.5 text-left wrap-anywhere hover:bg-secondary focus-visible:outline-2 focus-visible:outline-ring pointer-coarse:min-h-11">
              <Icon aria-hidden="true" className={cn('size-3.5 shrink-0', state === 'linked' ? 'text-emerald-700 dark:text-emerald-400'
                : ['conflict', 'error'].includes(state) ? 'text-destructive' : 'text-muted-foreground')} />
              <span>{item.name}</span><span className="sr-only"> · {label}</span>
            </button>
          </li>
        })}
        {rows.length === 0 && <li className="text-muted-foreground">Brak instrukcji</li>}
      </ul>
      <Button type="button" size="icon" variant="ghost" className={cn('size-7', shortcutStyle)}
        aria-label="Szczegóły instrukcji" aria-expanded={expanded} aria-controls={detail} title="Szczegóły instrukcji"
        onClick={() => setExpanded(value => !value)}>
        <ChevronDown className={cn(expanded && 'rotate-180')} />
      </Button>
      {(sending || project.pending || project.status === 'pending') && <span role="status" className="text-xs text-muted-foreground">Oczekuje na synchronizację</span>}
    </div>
    {(!extension.automatic || project.conflicts.length > 0 || error) && <div className="space-y-0.5 text-xs" role="status">
      {!extension.automatic && <p className="text-muted-foreground">Automatyzacja wyłączona dla rozszerzenia.</p>}
      {project.conflicts.length > 0 && <p className="wrap-anywhere text-destructive">Konflikty · {project.conflicts.join(', ')}</p>}
      {error && <p className="text-destructive">{instructionStatuses[project.status]}</p>}
    </div>}
    <div id={detail} hidden={!expanded} className="space-y-1 rounded-md bg-secondary/45 p-2 text-xs">
      <p>Zarządzane · {rows.filter(item => project.results[item.id] === 'linked').length} · Ręczne · {project.manual.length}</p>
      {!project.automatic && <p>Automatyzacja wyłączona dla projektu.</p>}
      {project.last_sync && <p>Ostatnia synchronizacja · <time dateTime={project.last_sync}>{timeLabel(project.last_sync)}</time></p>}
      {rows.some(item => project.wanted[item.id]) && <ul className="space-y-0.5">
        {rows.filter(item => project.wanted[item.id]).map(item => <li key={item.id}>{item.name} · {project.wanted[item.id]}</li>)}
      </ul>}
    </div>
    {selected && <InstructionDialog extension={extension} card={card} instruction={selected} close={() => setPreview(undefined)} />}
  </>
}

function ProjectExtensionCard({ extension, card, csrf, refresh }: {
  extension: ProjectExtension; card: CardDefinition; csrf: string; refresh: () => void
}) {
  const title = useId()
  const { send, pending } = useInstructionOperations(csrf, refresh)
  const body = <>
    <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
      <h3 id={title} className="flex min-w-0 items-center gap-1.5 text-sm font-semibold wrap-anywhere">
        <ExtensionIcon name={card.icon} />{card.title}
      </h3>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {card.sections.map(section => section.kind === 'instructions' &&
          <ProjectInstructionActions key={section.id} compact project={extension.project}
            operations={section.operations} pending={pending} send={data => { void send(extension.id, { ...data, card: card.id }) }} />,
        )}
      </div>
    </div>
    {card.sections.map(section => section.kind === 'instructions' &&
      <ProjectInstructionSection key={section.id} extension={extension} card={card} section={section} sending={pending} />,
    )}
  </>
  const className = 'min-w-0 space-y-0.5 px-2.5 py-1.5'
  return card.slot === 'project-details'
    ? <Card role="region" aria-labelledby={title} className={className}>{body}</Card>
    : <section aria-labelledby={title} className={className}>{body}</section>
}

export function ProjectExtensionCards({ slot, extensions, csrf, refresh }: {
  slot: Slot; extensions: ProjectExtension[]; csrf: string; refresh: () => void
}) {
  const cards = extensions.flatMap(extension => extension.cards
    .filter(card => card.slot === slot).map(card => ({ extension, card })))
  cards.sort((a, b) => a.card.order - b.card.order || a.extension.id.localeCompare(b.extension.id) || a.card.id.localeCompare(b.card.id))
  return <>{cards.map(({ extension, card }) =>
    <ProjectExtensionCard key={extension.project.id + '/' + extension.id + '/' + card.id}
      extension={extension} card={card} csrf={csrf} refresh={refresh} />,
  )}</>
}
