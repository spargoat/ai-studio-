
import type { HTMLAttributes, ReactNode } from 'react';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'interactive' | 'elevated' | 'outlined';
  padding?: 'none' | 'sm' | 'md' | 'lg';
  header?: ReactNode;
  footer?: ReactNode;
}

const cardVariants = {
  default: 'bg-zinc-900/60 border border-zinc-800 hover:border-zinc-600 card-shadow',
  interactive: 'bg-zinc-900/60 border border-zinc-800 hover:border-zinc-600 hover:bg-zinc-800/60 hover:-translate-y-0.5 cursor-pointer card-shadow card-shadow-hover',
  elevated: 'bg-zinc-900 border border-zinc-800 card-shadow',
  outlined: 'bg-transparent border border-zinc-700 hover:border-zinc-600',
};

const cardPadding = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

export function Card({ variant = 'default', padding = 'md', header, footer, className = '', children, ...props }: CardProps) {
  return (
    <div
      className={[
        'card-radius transition-all duration-200',
        cardVariants[variant],
        cardPadding[padding],
        className,
      ].join(' ')}
      {...props}
    >
      {header && <div className="pb-3 mb-3 border-b border-zinc-800">{header}</div>}
      {children}
      {footer && <div className="pt-3 mt-3 border-t border-zinc-800">{footer}</div>}
    </div>
  );
}

export default Card;


