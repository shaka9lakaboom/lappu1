'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export default function AuthPage() {
  const router = useRouter();
  const supabase = createClient();

  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [infoMsg, setInfoMsg] = useState<string | null>(null);
  const [sessionUser, setSessionUser] = useState<any | null>(null);

  useEffect(() => {
    async function checkSession() {
      const { data: { session } } = await supabase.auth.getSession();
      if (session?.user) {
        setSessionUser(session.user);
      }
      setInitialLoading(false);
    }
    checkSession();
  }, [supabase]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setErrorMsg(null);
    setInfoMsg(null);

    try {
      if (mode === 'signup') {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/auth/callback`,
          },
        });
        if (error) throw error;

        if (data.session) {
          setSessionUser(data.user);
          router.push('/dashboard');
        } else {
          setInfoMsg('Signup successful! Check your email for confirmation or log in if email confirmation is disabled.');
        }
      } else {
        const { data, error } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (error) throw error;

        if (data.session) {
          setSessionUser(data.user);
          router.push('/dashboard');
        }
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  const handleSignOut = async () => {
    setLoading(true);
    await supabase.auth.signOut();
    setSessionUser(null);
    setInfoMsg('Successfully signed out.');
    setLoading(false);
  };

  if (initialLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="flex items-center space-x-3 text-slate-400">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
          <span>Checking authentication state...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4 bg-slate-950">
      <Card className="w-full max-w-md border-slate-800 bg-slate-900/90 shadow-2xl">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold tracking-tight text-white">
            SkillMirror Auth (P0)
          </CardTitle>
          <CardDescription className="text-slate-400">
            {sessionUser
              ? `Currently authenticated as ${sessionUser.email}`
              : mode === 'signin'
              ? 'Sign in to access your SkillMirror account'
              : 'Create a new SkillMirror account'}
          </CardDescription>
        </CardHeader>

        <CardContent>
          {sessionUser ? (
            <div className="space-y-4">
              <div className="p-4 rounded-lg bg-slate-800/80 border border-slate-700">
                <p className="text-xs font-semibold uppercase text-slate-400">Status</p>
                <p className="text-sm font-medium text-green-400">Signed In</p>
                <p className="text-xs text-slate-400 mt-2">User ID:</p>
                <p className="text-xs font-mono text-slate-200 truncate">{sessionUser.id}</p>
              </div>

              <div className="flex gap-2">
                <Button
                  onClick={() => router.push('/dashboard')}
                  className="w-full"
                >
                  Go to Dashboard
                </Button>
                <Button
                  variant="outline"
                  onClick={handleSignOut}
                  disabled={loading}
                  className="w-full"
                >
                  Sign Out
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="flex rounded-lg bg-slate-950 p-1 border border-slate-800 mb-4">
                <button
                  type="button"
                  onClick={() => { setMode('signin'); setErrorMsg(null); setInfoMsg(null); }}
                  className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-all ${
                    mode === 'signin'
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  Sign In
                </button>
                <button
                  type="button"
                  onClick={() => { setMode('signup'); setErrorMsg(null); setInfoMsg(null); }}
                  className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-all ${
                    mode === 'signup'
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  Sign Up
                </button>
              </div>

              {errorMsg && (
                <div className="p-3 rounded-md bg-red-950/60 border border-red-800 text-xs text-red-300">
                  {errorMsg}
                </div>
              )}

              {infoMsg && (
                <div className="p-3 rounded-md bg-emerald-950/60 border border-emerald-800 text-xs text-emerald-300">
                  {infoMsg}
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="student@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>

              <Button type="submit" className="w-full" disabled={loading}>
                {loading
                  ? 'Processing...'
                  : mode === 'signin'
                  ? 'Sign In'
                  : 'Sign Up'}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
