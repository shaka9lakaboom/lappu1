import Link from 'next/link';

import { signUp } from '@/app/auth/actions';
import { AuthForm } from '@/components/auth-form';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function SignUpPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Create account</CardTitle>
          <CardDescription>New accounts start with the Student role.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <AuthForm mode="sign-up" action={signUp} />
          <p className="text-sm text-muted-foreground">
            Already registered?{' '}
            <Link href="/sign-in" className="underline underline-offset-4">
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
