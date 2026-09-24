'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ShieldCheck, Activity, Layers, Terminal } from 'lucide-react';

export default function HomePage() {
  const supabase = createClient();
  const [authState, setAuthState] = useState<'loading' | 'signed_out' | 'signed_in'>('loading');
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [backendHealth, setBackendHealth] = useState<any | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  useEffect(() => {
    async function checkAuth() {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.user) {
          setAuthState('signed_in');
          setUserEmail(session.user.email ?? 'Authenticated User');
        } else {
          setAuthState('signed_out');
        }
      } catch (err) {
        setAuthState('signed_out');
      }
    }

    async function checkBackendHealth() {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        const res = await fetch(`${apiUrl}/health`);
        if (res.ok) {
          const data = await res.json();
          setBackendHealth(data);
        } else {
          setBackendHealth({ status: 'error', code: res.status });
        }
      } catch (err) {
        setBackendHealth({ status: 'offline', message: 'Backend not running on localhost:8000' });
      } finally {
        setHealthLoading(false);
      }
    }

    checkAuth();
    checkBackendHealth();
  }, [supabase]);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Navigation Header */}
      <header className="border-b border-slate-800/80 bg-slate-900/60 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center font-bold text-white shadow-lg shadow-blue-500/30">
              SM
            </div>
            <span className="text-xl font-bold tracking-tight text-white">SkillMirror</span>
          </div>

          <div className="flex items-center space-x-4">
            {authState === 'loading' ? (
              <span className="text-xs text-slate-400">Loading auth...</span>
            ) : authState === 'signed_in' ? (
              <div className="flex items-center space-x-3">
                <span className="text-xs text-slate-300 bg-slate-800 px-3 py-1.5 rounded-full border border-slate-700">
                  {userEmail}
                </span>
                <Link href="/dashboard">
                  <Button size="sm">Dashboard</Button>
                </Link>
              </div>
            ) : (
              <Link href="/auth">
                <Button size="sm">Sign In / Sign Up</Button>
              </Link>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-12 space-y-12">
        {/* Hero Banner */}
        <section className="text-center space-y-4 py-8">
          <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-blue-950/80 border border-blue-800/60 text-blue-400 text-xs font-medium">
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>Development Phase P0 — Foundation Complete</span>
          </div>

          <h1 className="text-4xl md:text-6xl font-extrabold tracking-tight text-white bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
            SkillMirror Monorepo Shell
          </h1>
          <p className="max-w-2xl mx-auto text-slate-400 text-base md:text-lg">
            Mastery-based learning engine architecture establishing FastAPI backend, Next.js web application, Chrome Manifest V3 extension, and Supabase auth baseline.
          </p>
        </section>

        {/* System Verification Grid */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Web Auth Card */}
          <Card className="border-slate-800 bg-slate-900/60">
            <CardHeader>
              <div className="h-10 w-10 rounded-lg bg-blue-900/40 text-blue-400 flex items-center justify-center mb-2 border border-blue-800/40">
                <Layers className="w-5 h-5" />
              </div>
              <CardTitle className="text-lg">Web Application</CardTitle>
              <CardDescription>Next.js App Router + Supabase Auth</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 flex justify-between items-center text-xs">
                <span className="text-slate-400">Auth Session:</span>
                <span className={`font-semibold ${authState === 'signed_in' ? 'text-green-400' : 'text-amber-400'}`}>
                  {authState === 'loading' ? 'Loading' : authState === 'signed_in' ? 'Signed In' : 'Signed Out'}
                </span>
              </div>
              <Link href="/auth" className="block">
                <Button variant="outline" size="sm" className="w-full">
                  Manage Authentication
                </Button>
              </Link>
            </CardContent>
          </Card>

          {/* Backend Status Card */}
          <Card className="border-slate-800 bg-slate-900/60">
            <CardHeader>
              <div className="h-10 w-10 rounded-lg bg-indigo-900/40 text-indigo-400 flex items-center justify-center mb-2 border border-indigo-800/40">
                <Activity className="w-5 h-5" />
              </div>
              <CardTitle className="text-lg">FastAPI Backend</CardTitle>
              <CardDescription>GET /health endpoint verification</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-xs space-y-1">
                <div className="flex justify-between">
                  <span className="text-slate-400">Service Status:</span>
                  <span className="font-semibold text-green-400">
                    {healthLoading ? 'Checking...' : backendHealth?.status || 'Offline'}
                  </span>
                </div>
                {backendHealth?.version && (
                  <div className="flex justify-between text-slate-400">
                    <span>API Version:</span>
                    <span className="font-mono text-slate-200">{backendHealth.version}</span>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Extension Shell Card */}
          <Card className="border-slate-800 bg-slate-900/60">
            <CardHeader>
              <div className="h-10 w-10 rounded-lg bg-purple-900/40 text-purple-400 flex items-center justify-center mb-2 border border-purple-800/40">
                <Terminal className="w-5 h-5" />
              </div>
              <CardTitle className="text-lg">Chrome Extension</CardTitle>
              <CardDescription>Manifest V3 TypeScript Shell</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-xs space-y-1">
                <div className="flex justify-between text-slate-400">
                  <span>Manifest:</span>
                  <span className="font-semibold text-purple-300">V3</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Build Output:</span>
                  <span className="font-mono text-slate-200">apps/extension/dist</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </section>
      </main>
    </div>
  );
}
