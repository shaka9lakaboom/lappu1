// Deletes users created by e2e/auth-roundtrip.spec.ts (emails starting with
// "skillmirror-p0-"). Use after an aborted run. Reads the same env files as the
// test and never prints keys.
//   node e2e/cleanup-test-users.mjs          (from apps/web)
import { existsSync } from 'node:fs';

import { createClient } from '@supabase/supabase-js';

for (const file of ['.env.local', '.env.e2e.local']) if (existsSync(file)) process.loadEnvFile(file);

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) throw new Error('NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required');

const admin = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
const testUsers = [];
for (let page = 1; ; page++) {
  const { data, error } = await admin.auth.admin.listUsers({ page, perPage: 1000 });
  if (error) throw error;
  testUsers.push(...data.users.filter((u) => u.email?.startsWith('skillmirror-p0-')));
  if (data.users.length < 1000) break;
}

console.log(`found ${testUsers.length} E2E test user(s)`);
for (const user of testUsers) {
  const { error } = await admin.auth.admin.deleteUser(user.id);
  console.log(`${error ? 'FAILED ' : 'deleted'} ${user.email} (confirmed: ${Boolean(user.email_confirmed_at)})`);
  if (error) process.exitCode = 1;
}
