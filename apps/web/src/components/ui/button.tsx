import * as React from 'react';
import { cn } from '@skillmirror/ui';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'outline' | 'secondary' | 'ghost' | 'destructive';
  size?: 'default' | 'sm' | 'lg';
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', ...props }, ref) => {
    const variantStyles = {
      default: 'bg-blue-600 text-white hover:bg-blue-500 shadow-md shadow-blue-500/20',
      outline: 'border border-slate-700 bg-transparent text-slate-200 hover:bg-slate-800',
      secondary: 'bg-slate-800 text-slate-100 hover:bg-slate-700',
      ghost: 'bg-transparent text-slate-300 hover:bg-slate-800/60',
      destructive: 'bg-red-600 text-white hover:bg-red-500',
    };

    const sizeStyles = {
      default: 'h-10 px-4 py-2 text-sm font-medium',
      sm: 'h-8 px-3 text-xs font-medium',
      lg: 'h-12 px-6 text-base font-medium',
    };

    return (
      <button
        ref={ref}
        className={cn(
          'inline-flex items-center justify-center rounded-lg transition-all focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none',
          variantStyles[variant],
          sizeStyles[size],
          className
        )}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';

export { Button };
