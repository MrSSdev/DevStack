import { useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { request, runOperation } from '@/lib/api'
import { cn } from '@/lib/utils'

export type ProjectInstructionState = {
  id: string; slug: string; name: string; automatic: boolean; pending: boolean
  status: string; last_sync: string | null; wanted: Record<string, string>; results: Record<string, string>
  manual: string[]; conflicts: string[]; migratable: boolean
}

export type Operation = 'sync' | 'set_automatic' | 'migrate'
export type OperationData = { action: Operation; project?: string; enabled?: boolean; card?: string }

export const instructionStatuses: Record<string, string> = {
  pending: 'Oczekuje na synchronizację', synced: 'Zsynchronizowano', disabled: 'Wyłączone',
  manual: 'Podłączono ręcznie', conflict: 'Konflikt', error: 'Błąd synchronizacji',
  unsafe_directory: 'Nie można bezpiecznie otworzyć katalogu', directory_changed: 'Katalog projektu został zmieniony',
  missing: 'Brak katalogu projektu', linked: 'Zarządzane', detached: 'Odłączone', selected: 'Dobrano',
}

// Narrow windows and hybrid computers can expose a fine primary pointer.
export const touchHeight = 'max-sm:min-h-[44px] any-pointer-coarse:min-h-[44px]'
export const shortcutStyle = cn(touchHeight, 'max-sm:min-w-[44px] any-pointer-coarse:min-w-[44px]')

export function timeLabel(value: string) {
  return new Date(value).toLocaleString('pl-PL')
}

export function useInstructionOperations(csrf: string, onChange: () => void) {
  const [pending, setPending] = useState(false)
  const busy = useRef(false)
  async function send(extension: string, data: OperationData) {
    if (busy.current) return
    busy.current = true
    setPending(true)
    try {
      await runOperation('/api/extensions/instructions', csrf, { extension, ...data }, {
        title: 'Synchronizacja instrukcji',
        onResult: () => ({ kind: 'info', title: 'Przyjęto synchronizację. Wynik pojawi się przy projektach.' }),
      })
    } finally {
      busy.current = false
      setPending(false)
      onChange()
    }
  }
  return { pending, send }
}

export function ProjectInstructionActions({ project, operations, pending, send, compact = false }: {
  project: ProjectInstructionState; operations: Operation[]; pending: boolean
  send: (data: OperationData) => void; compact?: boolean
}) {
  const disabled = pending || project.pending
  const syncLabel = compact && !['error', 'unsafe_directory', 'directory_changed', 'missing'].includes(project.status)
    ? 'Synchronizuj instrukcje' : 'Ponów synchronizację'
  return <>
    {operations.includes('set_automatic') && <label className={compact ? cn('flex min-h-8 items-center gap-1.5 pl-1 text-xs', touchHeight) : 'flex items-center gap-2'}>
      <Switch disabled={disabled} checked={project.automatic} aria-label={'Automatyczne instrukcje w projekcie ' + project.name}
        onCheckedChange={enabled => send({ action: 'set_automatic', enabled, project: project.id })} />Automatycznie
    </label>}
    {operations.includes('sync') && <Button type="button" variant="outline" size={compact ? 'icon' : 'sm'} disabled={disabled}
      title={syncLabel} aria-label={syncLabel} className={compact ? shortcutStyle : undefined}
      onClick={() => send({ action: 'sync', project: project.id })}>
      {compact ? <RefreshCw /> : syncLabel}
    </Button>}
    {operations.includes('migrate') && project.migratable && <Button type="button" variant="outline" size="sm" disabled={disabled}
      className={compact ? shortcutStyle : undefined} onClick={() => {
        if (window.confirm('Zastąpić ręczne dowiązanie do zbiorczego skilla automatycznie dobranymi instrukcjami?')) {
          send({ action: 'migrate', project: project.id })
        }
      }}>Migruj ręczne dowiązanie</Button>}
  </>
}

export function InstructionPreview({ url, build }: { url: string; build: string }) {
  const [content, setContent] = useState('')
  const [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void request<{ text: string; build: string }>('GET', url, undefined, undefined, controller.signal)
      .then(value => {
        if (!controller.signal.aborted) {
          if (value.build === build) setContent(value.text)
          else setError(true)
        }
      })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [url, build])
  return <pre className="mt-2 max-h-[55dvh] overflow-auto whitespace-pre-wrap wrap-anywhere rounded-md bg-secondary p-3 text-xs" tabIndex={0}>
    {error ? 'Nie udało się pobrać aktualnej treści instrukcji.' : content || 'Wczytywanie instrukcji…'}
  </pre>
}
