import Link from 'next/link';
import { PRODUCT_NAME } from '@skillmirror/config';

import { buttonVariants } from '@/components/ui/button';

export default function LandingPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-8 px-6">
      <div className="space-y-4">
        <p className="text-sm font-medium uppercase tracking-widest text-muted-foreground">{PRODUCT_NAME}</p>
        <h1 className="text-4xl font-semibold tracking-tight">Use AI. Keep the skill.</h1>
        <p className="text-lg text-muted-foreground">
          SkillMirror separates what you can independently demonstrate from what you delegated to AI, and
          verifies the skills that still lack independent evidence.
        </p>
      </div>
      <div className="flex gap-3">
        <Link href="/sign-up" className={buttonVariants({ size: 'lg' })}>
          Create account
        </Link>
        <Link href="/sign-in" className={buttonVariants({ variant: 'outline', size: 'lg' })}>
          Sign in
        </Link>
      </div>
    </main>
  );
}
