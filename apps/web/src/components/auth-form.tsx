'use client';

import { useActionState } from 'react';

import type { AuthFormState } from '@/app/auth/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface AuthFormProps {
  mode: 'sign-in' | 'sign-up';
  action: (state: AuthFormState, formData: FormData) => Promise<AuthFormState>;
  next?: string;
}

export function AuthForm({ mode, action, next }: AuthFormProps) {
  const [state, formAction, pending] = useActionState(action, undefined);

  return (
    <form action={formAction} className="space-y-4">
      {next ? <input type="hidden" name="next" value={next} /> : null}

      {mode === 'sign-up' ? (
        <div className="space-y-2">
          <Label htmlFor="display_name">Display name (optional)</Label>
          <Input id="display_name" name="display_name" autoComplete="nickname" maxLength={120} />
        </div>
      ) : null}

      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" autoComplete="email" required />
      </div>

      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete={mode === 'sign-up' ? 'new-password' : 'current-password'}
          minLength={mode === 'sign-up' ? 8 : undefined}
          required
        />
      </div>

      {state?.error ? (
        <p role="alert" className="text-sm text-destructive">
          {state.error}
        </p>
      ) : null}
      {state?.message ? (
        <p role="status" className="text-sm text-muted-foreground">
          {state.message}
        </p>
      ) : null}

      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? 'Please wait…' : mode === 'sign-up' ? 'Create account' : 'Sign in'}
      </Button>
    </form>
  );
}
