/**
 * P0 exit gate: real Supabase authentication round trip.
 *
 *   sign up -> auth user created -> profile created -> session established
 *   -> protected dashboard -> refresh keeps session -> sign out removes session
 *   -> sign in again -> sign out
 *
 * Runs against a REAL Supabase project. Requires NEXT_PUBLIC_SUPABASE_URL and
 * NEXT_PUBLIC_SUPABASE_ANON_KEY (apps/web/.env.local) plus
 * SUPABASE_SERVICE_ROLE_KEY (apps/web/.env.e2e.local or the shell). The
 * service-role key is used only here, in Node, to confirm server-side state
 * and to delete the test user afterwards. It never reaches the browser.
 *
 * The project must have "Confirm email" disabled, or this test cannot
 * complete signup without an inbox.
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { expect, test, type Page } from '@playwright/test';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

const missingConfig = !url || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || !serviceRoleKey;
const configHint =
  'Requires a real Supabase project: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY';
// With E2E_REQUIRE=1 (CI) missing configuration is a failure, never a silent skip.
if (missingConfig && process.env.E2E_REQUIRE === '1') throw new Error(configHint);
test.skip(missingConfig, configHint);

async function authCookieNames(page: Page): Promise<string[]> {
  return (await page.context().cookies())
    .map((cookie) => cookie.name)
    .filter((name) => /^sb-.+-auth-token/.test(name));
}

test('sign up, persist, sign out, sign in, sign out', async ({ page }) => {
  const admin: SupabaseClient = createClient(url!, serviceRoleKey!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const email = `skillmirror-p0-${Date.now()}@${process.env.E2E_EMAIL_DOMAIN ?? 'example.com'}`;
  const password = `P0-${crypto.randomUUID()}`;
  const displayName = 'P0 Round Trip';
  let userId: string | undefined;

  try {
    // Protected route rejects anonymous visitors.
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/sign-in\?next=%2Fdashboard$/);

    // 1. Sign up through the real UI.
    await page.goto('/sign-up');
    await page.getByLabel('Display name (optional)').fill(displayName);
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Create account' }).click();

    const confirmationNotice = page.getByText('Check your email');
    await expect(page.getByTestId('user-id').or(confirmationNotice)).toBeVisible();
    if (await confirmationNotice.isVisible()) {
      throw new Error('Supabase "Confirm email" is enabled; disable it for the dev project to run this gate.');
    }

    // 2. Session established and protected dashboard reachable.
    await expect(page).toHaveURL(/\/dashboard$/);
    userId = (await page.getByTestId('user-id').textContent())?.trim();
    expect(userId).toMatch(/^[0-9a-f-]{36}$/);
    await expect(page.getByTestId('user-email')).toHaveText(email);
    expect(await authCookieNames(page)).not.toHaveLength(0);

    // 3. Supabase Auth user exists server-side.
    const { data: authUser, error: authError } = await admin.auth.admin.getUserById(userId!);
    expect(authError).toBeNull();
    expect(authUser.user?.email).toBe(email);

    // 4. Profile row created by the trigger, visible to the user through RLS.
    const { data: profile, error: profileError } = await admin
      .from('profiles')
      .select('id, role, display_name')
      .eq('id', userId!)
      .single();
    expect(profileError).toBeNull();
    expect(profile).toEqual({ id: userId, role: 'STUDENT', display_name: displayName });
    await expect(page.getByTestId('profile-role')).toHaveText('STUDENT');
    await expect(page.getByTestId('profile-display-name')).toHaveText(displayName);

    // 5. Refresh keeps the same session.
    await page.reload();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByTestId('user-id')).toHaveText(userId!);

    // 6. Sign out removes the session.
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
    expect(await authCookieNames(page)).toHaveLength(0);
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/sign-in\?next=%2Fdashboard$/);

    // 7. Sign in again with the same credentials, honouring ?next=.
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByTestId('user-id')).toHaveText(userId!);

    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
  } finally {
    if (userId) {
      const { error } = await admin.auth.admin.deleteUser(userId);
      expect(error, 'cleanup: delete test user').toBeNull();
    }
  }
});
