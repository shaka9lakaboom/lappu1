/**
 * SkillMirror Canonical Contracts Foundation (P0)
 */

export enum UserRole {
  STUDENT = 'student',
  TEACHER = 'teacher',
  ADMIN = 'admin',
}

export enum Provider {
  CHATGPT = 'chatgpt',
  CLAUDE = 'claude',
}

export interface ApiHealthResponse {
  status: string;
  service: string;
  environment: string;
  version: string;
  timestamp: string;
}

export interface UserSessionState {
  isAuthenticated: boolean;
  userId: string | null;
  email: string | null;
  role?: UserRole;
}

export interface BaseEventPayload {
  provider: Provider;
  sessionId: string;
  timestamp: string;
}
