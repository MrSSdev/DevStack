import type { ReactNode } from 'react'

// Every work view shares the same title/action layout. Actions wrap rather
// than shrinking the title or forcing horizontal scrolling in split windows.
export function PageHeader({title,actions}:{title:string;actions?:ReactNode}){
  return <header data-slot="page-header" className="flex min-h-8 min-w-0 flex-wrap items-center justify-between gap-2">
    <h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{title}</h1>
    {actions&&<div data-slot="page-actions" className="flex min-w-0 flex-wrap items-center gap-1.5">{actions}</div>}
  </header>
}
