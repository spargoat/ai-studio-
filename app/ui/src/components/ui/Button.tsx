
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success';
type Size = 'xs' | 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconRight?: ReactNode;
  loading?: boolean;
  fullWidth?: boolean;
}

const variants: Record<Variant, string> = {
  primary:
    'bg-violet-600 hover:bg-violet-500 active:bg-violet-700 text-white shadow-sm shadow-violet-600/25 hover:shadow-lg hover:shadow-violet-600/20',
  secondary:
    'bg-zinc-800 hover:bg-zinc-700 active:bg-zinc-800 text-zinc-200 border border-zinc-700 hover:border-zinc-600',
  ghost:
    'bg-transparent hover:bg-zinc-800/60 active:bg-zinc-800 text-zinc-400 hover:text-zinc-200',
  danger:
    'bg-red-600/90 hover:bg-red-500 active:bg-red-700 text-white shadow-sm shadow-red-600/20',
  success:
    'bg-emerald-600/90 hover:bg-emerald-500 active:bg-emerald-700 text-white shadow-sm shadow-emerald-600/20',
};

const sizes: Record<Size, string> = {
  xs: 'h-7 px-2 text-xs gap-1 btn-radius',
  sm: 'h-8 px-3 text-xs gap-1.5 btn-radius',
  md: 'h-9 px-4 text-sm gap-2 btn-radius',
  lg: 'h-11 px-6 text-sm gap-2 btn-radius',
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { variant = 'primary', size = 'md', icon, iconRight, loading, fullWidth, className = '', children, disabled, ...props },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={[
          'inline-flex items-center justify-center font-medium btn-transition select-none',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 focus-visible:ring-offset-1 focus-visible:ring-offset-zinc-900',
          'disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none',
          variants[variant],
          sizes[size],
          fullWidth ? 'w-full' : '',
          className,
        ].join(' ')}
        {...props}
      >
        {loading ? <Loader2 size={size === 'xs' ? 12 : 14} className="animate-spin" /> : icon}
        {children}
        {iconRight}
      </button>
    );
  },
);

Button.displayName = 'Button';
export default Button;
export { Button };
export type { ButtonProps, Variant as ButtonVariant, Size as ButtonSize };


