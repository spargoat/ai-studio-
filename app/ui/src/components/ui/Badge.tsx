
import type { HTMLAttributes } from 'react';

type BadgeVariant = 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
type BadgeSize = 'sm' | 'md';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
  dot?: boolean;
}

const badgeVariants: Record<BadgeVariant, string> = {
  default: 'bg-zinc-800 text-zinc-300 border-zinc-700',
  primary: 'bg-violet-600/15 text-violet-400 border-violet-500/30',
  success: 'bg-emerald-600/15 text-emerald-400 border-emerald-500/30',
  warning: 'bg-amber-600/15 text-amber-400 border-amber-500/30',
  danger: 'bg-red-600/15 text-red-400 border-red-500/30',
  info: 'bg-blue-600/15 text-blue-400 border-blue-500/30',
};

const dotColors: Record<BadgeVariant, string> = {
  default: 'bg-zinc-400',
  primary: 'bg-violet-400',
  success: 'bg-emerald-400',
  warning: 'bg-amber-400',
  danger: 'bg-red-400',
  info: 'bg-blue-400',
};

const badgeSizes: Record<BadgeSize, string> = {
  sm: 'text-[10px] px-1.5 py-0.5',
  md: 'text-xs px-2 py-0.5',
};

export function Badge({ variant = 'default', size = 'md', dot, className = '', children, ...props }: BadgeProps) {
  return (
    <span
      className={[
        'inline-flex items-center gap-1.5 font-medium rounded-full border',
        badgeVariants[variant],
        badgeSizes[size],
        className,
      ].join(' ')}
      {...props}
    >
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${dotColors[variant]}`} />}
      {children}
    </span>
  );
}

export default Badge;


