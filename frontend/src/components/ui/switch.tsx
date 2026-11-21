import * as React from 'react'
import { cn } from '@/lib/utils'
export function Switch({ checked, onCheckedChange, className, disabled, ...props }: Omit<React.ComponentProps<'button'>,'onChange'> & {checked:boolean;onCheckedChange:(checked:boolean)=>void}) {
  return <button role="switch" type="button" aria-checked={checked} data-state={checked?'checked':'unchecked'} disabled={disabled} onClick={()=>onCheckedChange(!checked)} className={cn('relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border-2 border-transparent bg-input transition-colors data-[state=checked]:bg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',className)} {...props}><span className="pointer-events-none size-5 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-5" data-state={checked?'checked':'unchecked'}/></button>
}
