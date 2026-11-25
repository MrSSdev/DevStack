import * as React from 'react'
import { cn } from '@/lib/utils'
export function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) { return <textarea data-slot="textarea" className={cn('min-h-24 w-full rounded-lg border bg-background px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',className)} {...props} /> }
