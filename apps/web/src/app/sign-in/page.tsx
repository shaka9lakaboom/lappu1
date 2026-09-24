import Link from 'next/link';

import { signIn } from '@/app/auth/actions';
import { AuthForm } from '@/components/auth-form';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { safeRedirectPath } from '@/lib/redirect';

export default async function SignInPage({ searchParams }: PageProps<'/sign-in'>) {
  const { next, error } = await searchParams;

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Sign in to your SkillMirror account.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {typeof error === 'string' ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <AuthForm mode="sign-in" action={signIn} next={safeRedirectPath(next)} />
          <p className="text-sm text-muted-foreground">
            No account?{' '}
            <Link href="/sign-up" className="underline underline-offset-4">
              Create one
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
