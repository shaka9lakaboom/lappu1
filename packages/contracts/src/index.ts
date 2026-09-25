/**
 * Canonical SkillMirror contracts.
 *
 * Values here mirror the database enums in supabase/migrations and the
 * backend Pydantic models. Change them together, never independently.
 */

/** Matches the `public.app_role` Postgres enum. */
export const USER_ROLES = ['STUDENT', 'TEACHER', 'ADMIN'] as const;
export type UserRole = (typeof USER_ROLES)[number];

/** Deployment environments accepted by the backend `APP_ENV` setting. */
export const APP_ENVIRONMENTS = ['development', 'test', 'staging', 'production'] as const;
export type AppEnvironment = (typeof APP_ENVIRONMENTS)[number];

/** Source providers from the RawActivityEnvelope contract (architecture §6.5). */
export const SOURCE_PROVIDERS = ['chatgpt', 'claude', 'gemini', 'skillmirror'] as const;
export type SourceProvider = (typeof SOURCE_PROVIDERS)[number];

/** Body of GET /health. Validated against schemas/health-response.schema.json. */
export interface HealthResponse {
  status: 'ok';
  service: string;
  environment: AppEnvironment;
  version: string;
  timestamp: string;
}

/** Row shape of `public.profiles` as readable by its owner. */
export interface Profile {
  id: string;
  role: UserRole;
  display_name: string | null;
  timezone: string;
  created_at: string;
  updated_at: string;
}

export * from './raw-activity';
export * from './courses';
export * from './intelligence';
export * from './experience';
