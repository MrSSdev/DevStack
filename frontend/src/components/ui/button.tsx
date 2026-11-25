import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const variants = cva('inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4', {
  variants: {
    variant: {
      default: 'bg-primary text-primary-foreground hover:bg-primary/90',
      secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
      outline: 'border bg-background hover:bg-accent hover:text-accent-foreground',
      ghost: 'hover:bg-accent hover:text-accent-foreground',
      destructive: 'bg-destructive text-white hover:bg-destructive/90',
    },
    size: { default: 'h-9 px-3 py-1.5', sm: 'h-8 rounded-md px-2.5', lg: 'h-11 px-5', icon: 'size-8' },
  }, defaultVariants: { variant: 'default', size: 'default' }
})
export function Button({ className, variant, size, ...props }: React.ComponentProps<'button'> & VariantProps<typeof variants>) {
  return <button data-slot="button" className={cn(variants({ variant, size, className }))} {...props} />
}
