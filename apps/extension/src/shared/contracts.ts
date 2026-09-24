/**
 * SkillMirror Extension Shared Contracts Baseline (P0)
 */

export enum ExtensionProvider {
  CHATGPT = 'chatgpt',
  CLAUDE = 'claude',
}

export interface ExtensionState {
  isCapturing: boolean;
  activeProvider: ExtensionProvider | null;
  queueLength: number;
}
