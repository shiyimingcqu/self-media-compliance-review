import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ButtonHTMLAttributes } from 'react';
import { cn } from '../../shared/lib/cn';
const variants = cva('ui-button inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-[13px] font-medium disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring', {
  variants: { variant: { default: 'bg-primary text-primary-foreground hover:bg-[var(--accent-hover)]', outline: 'border border-border bg-transparent text-secondary-foreground hover:bg-secondary', ghost: 'border-0 bg-transparent text-secondary-foreground hover:bg-secondary', destructive: 'border border-[var(--state-fail)] bg-transparent text-destructive hover:bg-[var(--fail-muted)]' }, size: { default: 'h-8 px-3', sm: 'h-7 px-2 text-xs', icon: 'h-8 w-8 p-0' } }, defaultVariants: { variant: 'default', size: 'default' },
});
export function Button({ className, variant, size, asChild, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof variants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button';
  return <Comp type="button" className={cn(variants({ variant, size }), className)} {...props} />;
}
