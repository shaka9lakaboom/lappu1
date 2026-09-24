import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge Tailwind class names (the shadcn/ui `cn` helper). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
