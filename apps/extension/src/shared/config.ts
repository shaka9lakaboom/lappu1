/**
 * Build-time configuration, injected by build.mjs. Only public values: the
 * Supabase project URL and anon (publishable) key, the SkillMirror API URL and
 * the web app URL. No secret or service-role key can reach the bundle
 * (build.mjs refuses them).
 */
export interface CompanionConfig {
  supabaseUrl: string | null;
  supabaseAnonKey: string | null;
  apiUrl: string | null;
  webUrl: string | null;
}

declare const __SKILLMIRROR_CONFIG__: CompanionConfig | undefined;

export const CONFIG: CompanionConfig =
  typeof __SKILLMIRROR_CONFIG__ !== 'undefined'
    ? __SKILLMIRROR_CONFIG__
    : { supabaseUrl: null, supabaseAnonKey: null, apiUrl: null, webUrl: null };

export function isConfigured(config: CompanionConfig = CONFIG): boolean {
  return Boolean(config.supabaseUrl && config.supabaseAnonKey && config.apiUrl);
}
