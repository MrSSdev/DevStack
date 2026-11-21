import * as React from 'react'
import { cn } from '@/lib/utils'

/** Accessible two-state control with explicit checked/unchecked contrast in both themes. */
export function Switch({ checked, onCheckedChange, className, disabled, ...props }: Omit<React.ComponentProps<'button'>,'onChange'> & {checked:boolean;onCheckedChange:(checked:boolean)=>void}) {
  return <button
    role="switch"
    type="button"
    aria-checked={checked}
    data-state={checked?'checked':'unchecked'}
    disabled={disabled}
    onClick={()=>onCheckedChange(!checked)}
    className={cn(
      'relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border-2 transition-colors duration-150',
      'border-zinc-400 bg-zinc-400 dark:border-zinc-600 dark:bg-zinc-700',
      'data-[state=checked]:border-emerald-600 data-[state=checked]:bg-emerald-600',
      'dark:data-[state=checked]:border-emerald-500 dark:data-[state=checked]:bg-emerald-500',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50',
      className,
    )}
    {...props}
  >
    <span className="pointer-events-none size-5 translate-x-0.5 rounded-full bg-white shadow-sm transition-transform duration-150 data-[state=checked]:translate-x-[22px]" data-state={checked?'checked':'unchecked'}/>
  </button>
}
