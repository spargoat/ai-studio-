
import { forwardRef, type InputHTMLAttributes } from 'react';

type InputSize = 'sm' | 'md' | 'lg';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
  inputSize?: InputSize;
  icon?: React.ReactNode;
}

const inputSizes: Record<InputSize, string> = {
  sm: 'h-8 px-3 text-xs input-radius',
  md: 'h-9 px-3 text-sm input-radius',
  lg: 'h-11 px-4 text-sm input-radius',
};

import React from 'react';

const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, hint, inputSize = 'md', icon, className = '', ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1.5">
        {label && (
          <label className="text-xs font-medium text-zinc-400">{label}</label>
        )}
        <div className="relative">
          {icon && (
            <div className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500">{icon}</div>
          )}
          <input
            ref={ref}
            className={[
              'w-full bg-zinc-800/80 border text-white placeholder-zinc-500',
              'input-transition',
              'focus:outline-none focus:ring-2 focus:ring-violet-500/30 focus:border-violet-500',
              'hover:border-zinc-600',
              error ? 'border-red-500/60' : 'border-zinc-700',
              inputSizes[inputSize],
              icon ? 'pl-9' : '',
              className,
            ].join(' ')}
            {...props}
          />
        </div>
        {error && <p className="text-xs text-red-400">{error}</p>}
        {hint && !error && <p className="text-xs text-zinc-500">{hint}</p>}
      </div>
    );
  },
);

Input.displayName = 'Input';
export default Input;
export { Input };


