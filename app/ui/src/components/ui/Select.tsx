
import { forwardRef, type SelectHTMLAttributes } from 'react';
import { ChevronDown } from 'lucide-react';

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: { value: string; label: string }[];
  selectSize?: 'sm' | 'md' | 'lg';
}

const selectSizes = {
  sm: 'h-8 px-3 text-xs rounded-lg',
  md: 'h-9 px-3 text-sm rounded-lg',
  lg: 'h-11 px-4 text-sm rounded-xl',
};

const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, error, options, selectSize = 'md', className = '', ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1.5">
        {label && <label className="text-xs font-medium text-zinc-400">{label}</label>}
        <div className="relative">
          <select
            ref={ref}
            className={[
              'w-full appearance-none bg-zinc-800/80 border text-white cursor-pointer',
              'transition-all duration-150 pr-8',
              'focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500/30',
              'hover:border-zinc-600',
              error ? 'border-red-500/60' : 'border-zinc-700',
              selectSizes[selectSize],
              className,
            ].join(' ')}
            {...props}
          >
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none" />
        </div>
        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    );
  },
);

Select.displayName = 'Select';
export default Select;
export { Select };


