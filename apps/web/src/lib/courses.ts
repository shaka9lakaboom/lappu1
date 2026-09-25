import {
  COURSE_LIMITS,
  type Course,
  type CourseCreateRequest,
  type CourseGraphStatus,
  type CourseSkill,
  type CourseSkillsResponse,
  type SkillNode,
} from '@skillmirror/contracts';

const TOPIC_KINDS = new Set(['DOMAIN', 'SUBJECT', 'TOPIC']);
const IN_PROGRESS: ReadonlySet<CourseGraphStatus> = new Set(['PENDING', 'GENERATING', 'EMBEDDING']);

export function isGraphInProgress(status: CourseGraphStatus): boolean {
  return IN_PROGRESS.has(status);
}

type BootstrapView = Pick<Course, 'graph_status' | 'bootstrap_job_state'> &
  Partial<Pick<Course, 'bootstrap_wait_reason' | 'bootstrap_next_attempt_at' | 'graph_error'>>;

/** Bootstrap status in plain words. */
export function graphStatusLabel(course: BootstrapView): string {
  const status = course.graph_status;
  if (status === 'READY') return 'Skill graph ready';
  if (status === 'FAILED') return 'Skill graph generation failed';
  const indexing = status === 'EMBEDDING';
  if (course.bootstrap_job_state === 'PROCESSING') {
    return indexing ? 'Indexing skills for retrieval' : 'Generating skill graph';
  }
  if (course.bootstrap_wait_reason === 'MODEL_BACKPRESSURE' || course.bootstrap_wait_reason === 'MODEL_BUDGET_RESERVE') {
    return indexing ? 'Waiting to retry skill indexing' : 'Waiting to retry skill graph generation';
  }
  if (course.bootstrap_job_state === 'RETRY_WAIT') {
    return indexing ? 'Retrying skill indexing' : 'Retrying skill graph generation';
  }
  if (indexing) return 'Indexing skills for retrieval';
  return status === 'PENDING' ? 'Queued for skill graph generation' : 'Generating skill graph';
}

/** A job due for longer than this without being claimed means no worker is polling the queue. */
export const WORKER_OVERDUE_MS = 60_000;

export interface GraphWait {
  /** What the bootstrap waits for; null when it is simply queued. */
  message: string | null;
  /** When the next automatic attempt is due (ISO). */
  nextAttemptAt: string;
  /** Due for over a minute and still not claimed: the backend worker does not seem to run. */
  overdue: boolean;
}

/** Why an in-progress bootstrap is not running right now, from the API's job summary. */
export function graphWait(course: BootstrapView, now: Date = new Date()): GraphWait | null {
  const nextAttemptAt = course.bootstrap_next_attempt_at;
  if (!nextAttemptAt || !isGraphInProgress(course.graph_status)) return null;
  const subject = course.graph_status === 'EMBEDDING' ? 'Skill indexing' : 'Skill graph generation';
  let message: string | null = null;
  switch (course.bootstrap_wait_reason) {
    case 'MODEL_BACKPRESSURE':
      message = `${subject} is temporarily waiting for the AI provider.`;
      break;
    case 'MODEL_BUDGET_RESERVE':
      message = `${subject} is waiting for the AI request budget: today's free-tier allowance for this model is used up.`;
      break;
    case 'RETRY_AFTER_ERROR':
      message = `The last attempt did not succeed.${course.graph_error ? ` ${course.graph_error}` : ''}`;
      break;
  }
  const overdue = now.getTime() - Date.parse(nextAttemptAt) > WORKER_OVERDUE_MS;
  return { message, nextAttemptAt, overdue };
}

export function importanceLabel(importance: number): string {
  if (importance >= 0.8) return 'Core';
  if (importance >= 0.6) return 'Important';
  return 'Standard';
}

export interface TopicGroup {
  topic: SkillNode | null;
  skills: CourseSkill[];
}

/** Assessable skills grouped under their topic (PARENT edges); skills without a topic go last. */
export function groupSkillsByTopic(graph: CourseSkillsResponse): TopicGroup[] {
  const topics = new Map<string, SkillNode>();
  const assessable: CourseSkill[] = [];
  for (const entry of graph.skills) {
    if (TOPIC_KINDS.has(entry.skill.node_kind)) topics.set(entry.skill.id, entry.skill);
    else assessable.push(entry);
  }
  const topicOf = new Map<string, string>();
  for (const edge of graph.edges) {
    if (edge.edge_type === 'PARENT' && topics.has(edge.from_skill_id) && !topicOf.has(edge.to_skill_id)) {
      topicOf.set(edge.to_skill_id, edge.from_skill_id);
    }
  }
  const byImportance = (a: CourseSkill, b: CourseSkill) =>
    b.importance - a.importance || a.skill.canonical_name.localeCompare(b.skill.canonical_name);

  const groups: TopicGroup[] = [...topics.values()]
    .sort((a, b) => a.canonical_name.localeCompare(b.canonical_name))
    .map((topic) => ({
      topic,
      skills: assessable.filter((s) => topicOf.get(s.skill.id) === topic.id).sort(byImportance),
    }))
    .filter((group) => group.skills.length > 0);
  const orphans = assessable.filter((s) => !topicOf.has(s.skill.id)).sort(byImportance);
  if (orphans.length > 0) groups.push({ topic: null, skills: orphans });
  return groups;
}

/** Prerequisite names per skill id, from PREREQUISITE edges. */
export function prerequisiteNames(graph: CourseSkillsResponse): Map<string, string[]> {
  const names = new Map(graph.skills.map((s) => [s.skill.id, s.skill.canonical_name]));
  const result = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.edge_type !== 'PREREQUISITE') continue;
    const name = names.get(edge.from_skill_id);
    if (!name) continue;
    result.set(edge.to_skill_id, [...(result.get(edge.to_skill_id) ?? []), name].sort());
  }
  return result;
}

function optional(value: FormDataEntryValue | null): string | null {
  const text = typeof value === 'string' ? value.trim() : '';
  return text.length > 0 ? text : null;
}

export type CourseFormResult = { ok: true; request: CourseCreateRequest } | { ok: false; error: string };

/** Validates the create-course form the same way the API does; empty optional fields become null. */
export function buildCourseRequest(formData: FormData): CourseFormResult {
  const name = optional(formData.get('name'));
  if (!name) return { ok: false, error: 'Course name is required.' };
  const request: CourseCreateRequest = {
    name,
    subject: optional(formData.get('subject')),
    level: optional(formData.get('level')),
    description: optional(formData.get('description')),
  };
  const limits: [keyof CourseCreateRequest, number, string][] = [
    ['name', COURSE_LIMITS.nameMaxChars, 'Course name'],
    ['subject', COURSE_LIMITS.subjectMaxChars, 'Subject'],
    ['level', COURSE_LIMITS.levelMaxChars, 'Level'],
    ['description', COURSE_LIMITS.descriptionMaxChars, 'Description'],
  ];
  for (const [field, max, label] of limits) {
    const value = request[field];
    if (value && value.length > max) return { ok: false, error: `${label} must be at most ${max} characters.` };
  }
  return { ok: true, request };
}
