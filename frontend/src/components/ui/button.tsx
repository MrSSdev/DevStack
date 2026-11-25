import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const variants = cva('inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0', {
  variants: {
    variant: {
      default: 'border border-transparent bg-primary text-primary-foreground hover:bg-button-primary-hover',
      secondary: 'border border-button-border bg-button-surface text-foreground hover:bg-button-hover',
      outline: 'border border-button-border bg-button-surface text-foreground hover:bg-button-hover',
      ghost: 'border border-transparent bg-button-surface/60 text-foreground hover:bg-button-hover',
      destructive: 'bg-destructive text-white hover:bg-destructive/90',
    },
    size: { default: 'h-9 px-3 py-1.5', sm: 'h-8 rounded-md px-2.5', lg: 'h-11 px-5', icon: 'size-8' },
  }, defaultVariants: { variant: 'default', size: 'default' }
})
export function Button({ className, variant, size, ...props }: React.ComponentProps<'button'> & VariantProps<typeof variants>) {
  return <button data-slot="button" className={cn(variants({ variant, size, className }))} {...props} />
}
