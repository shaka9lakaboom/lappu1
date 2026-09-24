/**
 * Shared SkillMirror configuration constants.
 *
 * Tunable policy values (thresholds, weights) belong in the `policy_config`
 * table once it exists; only structural constants live here.
 */

export const PRODUCT_NAME = 'SkillMirror';

/** All versioned backend endpoints are mounted under this prefix (architecture §13). */
export const API_V1_PREFIX = '/v1';
