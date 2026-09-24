'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function DashboardPage() {
  const router = useRouter();
  const supabase = createClient();
  const [user, setUser] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadUser() {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        router.push('/auth');
      } else {
        setUser(session.user);
      }
      setLoading(false);
    }
    loadUser();
  }, [router, supabase]);

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push('/auth');
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 p-4">
        <div className="flex items-center space-x-3 text-slate-400">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
          <span>Loading authenticated session...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 p-6 md:p-12 text-slate-100">
      <div className="max-w-4xl mx-auto space-y-8">
        <div className="flex items-center justify-between border-b border-slate-800 pb-6">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-white">SkillMirror Dashboard</h1>
            <p className="text-sm text-slate-400 mt-1">P0 Foundation Verified — Session Active</p>
          </div>
          <Button variant="outline" onClick={handleSignOut}>
            Sign Out
          </Button>
        </div>

        <Card className="border-slate-800 bg-slate-900/80">
          <CardHeader>
            <CardTitle className="text-xl">Authentication Round-Trip State</CardTitle>
            <CardDescription className="text-slate-400">
              Verified active session surviving navigation
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 rounded-lg bg-slate-950 border border-slate-800">
                <span className="text-xs text-slate-400 font-mono uppercase">User ID</span>
                <p className="text-sm font-mono text-slate-200 mt-1 truncate">{user?.id}</p>
              </div>

              <div className="p-4 rounded-lg bg-slate-950 border border-slate-800">
                <span className="text-xs text-slate-400 font-mono uppercase">Email Address</span>
                <p className="text-sm font-mono text-slate-200 mt-1">{user?.email}</p>
              </div>
            </div>

            <div className="p-4 rounded-lg bg-emerald-950/40 border border-emerald-800/60 text-emerald-300 text-xs">
              ✓ Supabase Auth session active and loaded successfully into Next.js App Router context.
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
