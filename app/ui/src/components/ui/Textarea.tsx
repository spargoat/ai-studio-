
import { forwardRef, type TextareaHTMLAttributes } from 'react';

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  hint?: string;
}

const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, hint, className = '', ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1.5">
        {label && <label className="text-xs font-medium text-zinc-400">{label}</label>}
        <textarea
          ref={ref}
          className={[
            'w-full bg-zinc-800/80 border text-white placeholder-zinc-500 rounded-lg px-3 py-2 text-sm',
            'transition-all duration-150 resize-y min-h-[80px]',
            'focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500/30',
            'hover:border-zinc-600',
            error ? 'border-red-500/60' : 'border-zinc-700',
            className,
          ].join(' ')}
          {...props}
        />
        {error && <p className="text-xs text-red-400">{error}</p>}
        {hint && !error && <p className="text-xs text-zinc-500">{hint}</p>}
      </div>
    );
  },
);

Textarea.displayName = 'Textarea';
export default Textarea;
export { Textarea };


