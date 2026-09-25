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

/** Bootstrap status in plain words. */
export function graphStatusLabel(course: Pick<Course, 'graph_status' | 'bootstrap_job_state'>): string {
  const retrying = course.bootstrap_job_state === 'RETRY_WAIT';
  switch (course.graph_status) {
    case 'PENDING':
      return retrying ? 'Retrying skill graph generation' : 'Queued for skill graph generation';
    case 'GENERATING':
      return retrying ? 'Retrying skill graph generation' : 'Generating skill graph';
    case 'EMBEDDING':
      return retrying ? 'Retrying skill indexing' : 'Indexing skills for retrieval';
    case 'READY':
      return 'Skill graph ready';
    case 'FAILED':
      return 'Skill graph generation failed';
  }
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
