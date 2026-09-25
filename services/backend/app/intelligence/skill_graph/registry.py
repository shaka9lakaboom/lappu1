"""Global skill registry: canonicalization, course overlay, rename and merge (§8).

The canonicalizer is the only code that creates registry nodes from a model
proposal. It resolves every proposed name and alias against the registry by
canonical key (`skill_key`), reuses existing UUIDs, attaches new aliases to
the canonical UUID, refuses alias keys owned by another skill, keeps PARENT
and PREREQUISITE edges acyclic across the whole registry, and writes the
course overlay (`course_skills`). All of it runs in the caller's transaction.
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from psycopg import Connection

from app.intelligence.policy import SkillGraphPolicy
from app.intelligence.skill_graph.canonical import skill_key, slugify
from app.intelligence.skill_graph.schemas import GraphProposal

ASSESSABLE_KINDS = ("SKILL", "SUBSKILL")


@dataclass
class BootstrapReport:
    graph_version: int
    created_skills: list[UUID] = field(default_factory=list)
    reused_skills: list[UUID] = field(default_factory=list)
    created_topics: list[UUID] = field(default_factory=list)
    reused_topics: list[UUID] = field(default_factory=list)
    aliases_added: int = 0
    alias_conflicts: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_skipped: list[str] = field(default_factory=list)
    course_skill_ids: list[UUID] = field(default_factory=list)

    @property
    def assessable_count(self) -> int:
        return len(self.created_skills) + len(self.reused_skills)


def resolve_keys(conn: Connection, keys: list[str]) -> dict[str, UUID]:
    """Canonical key -> live skill UUID (MERGED nodes resolve to their survivor)."""
    if not keys:
        return {}
    rows = conn.execute(
        """
        select a.normalized_alias, coalesce(n.merged_into_id, n.id)
          from public.skill_aliases a
          join public.skill_nodes n on n.id = a.skill_id
         where a.normalized_alias = any(%s)
        """,
        (list(set(keys)),),
    ).fetchall()
    return {key: skill_id for key, skill_id in rows}


def _unique_slug(conn: Connection, name: str) -> str:
    base = slugify(name)
    slug = base
    while conn.execute("select 1 from public.skill_nodes where slug = %s", (slug,)).fetchone():
        slug = f"{base[:90]}-{uuid4().hex[:6]}"
    return slug


def _create_node(
    conn: Connection,
    *,
    name: str,
    description: str,
    kind: str,
    course_id: UUID,
    model_run_id: UUID | None,
    difficulty_band: int | None = None,
    assessment_types: list[str] | None = None,
) -> UUID:
    (skill_id,) = conn.execute(
        """
        insert into public.skill_nodes (
            slug, canonical_name, normalized_name, description, node_kind, status,
            difficulty_band, assessment_types, source, source_course_id, source_model_run_id
        ) values (%s, %s, %s, %s, %s::public.skill_node_kind, 'ACTIVE',
                  %s, %s, 'COURSE_BOOTSTRAP', %s, %s)
        returning id
        """,
        (
            _unique_slug(conn, name),
            name,
            skill_key(name),
            description,
            kind,
            difficulty_band,
            assessment_types or [],
            course_id,
            model_run_id,
        ),
    ).fetchone()
    return skill_id


def add_alias(
    conn: Connection, skill_id: UUID, alias: str, *, source: str = "COURSE_BOOTSTRAP"
) -> tuple[bool, UUID | None]:
    """Attach an alias to a canonical skill. Returns (added, conflicting_owner)."""
    key = skill_key(alias)
    if not key:
        return False, None
    kind = "ABBREVIATION" if alias.isupper() and len(alias) <= 8 else "VARIANT"
    row = conn.execute(
        """
        insert into public.skill_aliases (skill_id, alias, normalized_alias, alias_kind, source)
        values (%s, %s, %s, %s::public.skill_alias_kind, %s::public.skill_source)
        on conflict (normalized_alias) do nothing
        returning id
        """,
        (skill_id, alias, key, kind, source),
    ).fetchone()
    if row:
        return True, None
    (owner,) = conn.execute(
        """
        select coalesce(n.merged_into_id, n.id) from public.skill_aliases a
          join public.skill_nodes n on n.id = a.skill_id where a.normalized_alias = %s
        """,
        (key,),
    ).fetchone()
    return False, (None if owner == skill_id else owner)


def _creates_cycle(conn: Connection, from_id: UUID, to_id: UUID, edge_type: str) -> bool:
    """Would from -> to close a cycle, i.e. does `to` already reach `from`?"""
    row = conn.execute(
        """
        with recursive reach(id) as (
            select to_skill_id from public.skill_edges
             where from_skill_id = %(to)s and edge_type = %(type)s::public.skill_edge_type
            union
            select e.to_skill_id from public.skill_edges e join reach r on e.from_skill_id = r.id
             where e.edge_type = %(type)s::public.skill_edge_type
        )
        select 1 from reach where id = %(from)s limit 1
        """,
        {"from": from_id, "to": to_id, "type": edge_type},
    ).fetchone()
    return row is not None


def add_edge(
    conn: Connection,
    from_id: UUID,
    to_id: UUID,
    edge_type: str,
    *,
    course_id: UUID | None,
    source: str = "COURSE_BOOTSTRAP",
) -> str:
    """Insert an edge; returns 'added', 'exists', 'self' or 'cycle'."""
    if from_id == to_id:
        return "self"
    if edge_type == "RELATED" and str(from_id) > str(to_id):
        from_id, to_id = to_id, from_id
    if edge_type in ("PARENT", "PREREQUISITE") and _creates_cycle(conn, from_id, to_id, edge_type):
        return "cycle"
    row = conn.execute(
        """
        insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source, course_id)
        values (%s, %s, %s::public.skill_edge_type, %s::public.skill_source, %s)
        on conflict (from_skill_id, to_skill_id, edge_type) do nothing
        returning id
        """,
        (from_id, to_id, edge_type, source, course_id),
    ).fetchone()
    return "added" if row else "exists"


def canonicalize_and_persist(
    conn: Connection,
    *,
    course_id: UUID,
    proposal: GraphProposal,
    policy: SkillGraphPolicy,
    model_run_id: UUID | None,
) -> BootstrapReport:
    """Write a validated proposal into the registry and the course overlay."""
    (current_version,) = conn.execute(
        "select graph_version from public.courses where id = %s for update", (course_id,)
    ).fetchone()
    report = BootstrapReport(graph_version=current_version + 1)

    # Resolve every proposed name and alias in one round trip.
    all_keys = [skill_key(t.name) for t in proposal.topics]
    for skill in proposal.skills:
        all_keys.append(skill_key(skill.canonical_name))
        all_keys.extend(skill_key(a) for a in skill.aliases)
    known = resolve_keys(conn, [k for k in all_keys if k])

    topic_ids: dict[str, UUID] = {}
    for topic in proposal.topics:
        existing = known.get(skill_key(topic.name))
        if existing:
            topic_ids[topic.key] = existing
            report.reused_topics.append(existing)
        else:
            topic_ids[topic.key] = _create_node(
                conn,
                name=topic.name,
                description=topic.description,
                kind="TOPIC",
                course_id=course_id,
                model_run_id=model_run_id,
            )
            known[skill_key(topic.name)] = topic_ids[topic.key]
            report.created_topics.append(topic_ids[topic.key])

    skill_ids: dict[str, UUID] = {}
    importance: dict[UUID, float] = {}
    for skill in proposal.skills:
        name_key = skill_key(skill.canonical_name)
        resolved = known.get(name_key)
        if resolved is None:
            # A proposal whose name is new but whose aliases all point at one
            # existing skill is a variant of that skill, not a new identity.
            owners = {known[k] for k in (skill_key(a) for a in skill.aliases) if k in known}
            if len(owners) == 1:
                resolved = owners.pop()
        if resolved is not None:
            skill_ids[skill.key] = resolved
            if resolved not in report.reused_skills and resolved not in report.created_skills:
                report.reused_skills.append(resolved)
            if name_key not in known:
                added, owner = add_alias(conn, resolved, skill.canonical_name)
                report.aliases_added += int(added)
                known[name_key] = resolved
        else:
            resolved = _create_node(
                conn,
                name=skill.canonical_name,
                description=skill.description,
                kind="SUBSKILL" if skill.parent_skill_key else "SKILL",
                course_id=course_id,
                model_run_id=model_run_id,
                difficulty_band=skill.difficulty_band,
                assessment_types=list(dict.fromkeys(skill.assessment_types)),
            )
            skill_ids[skill.key] = resolved
            known[name_key] = resolved
            report.created_skills.append(resolved)
        importance[resolved] = max(importance.get(resolved, 0.0), skill.importance)

        for alias in skill.aliases:
            alias_key = skill_key(alias)
            if not alias_key or known.get(alias_key) == resolved:
                continue
            added, owner = add_alias(conn, resolved, alias)
            if added:
                report.aliases_added += 1
                known[alias_key] = resolved
            elif owner is not None:
                report.alias_conflicts.append(f"{alias!r} already belongs to {owner}")

    # Edges: topic -> skill, parent skill -> sub-skill, prerequisites, related.
    edge_specs: list[tuple[UUID, UUID, str, str]] = []
    for skill in proposal.skills:
        target = skill_ids[skill.key]
        edge_specs.append((topic_ids[skill.topic_key], target, "PARENT", skill.key))
        if skill.parent_skill_key:
            edge_specs.append((skill_ids[skill.parent_skill_key], target, "PARENT", skill.key))
        for ref in skill.prerequisite_keys:
            edge_specs.append((skill_ids[ref], target, "PREREQUISITE", skill.key))
        for ref in skill.related_keys:
            edge_specs.append((skill_ids[ref], target, "RELATED", skill.key))
    for from_id, to_id, edge_type, label in edge_specs:
        outcome = add_edge(conn, from_id, to_id, edge_type, course_id=course_id)
        if outcome == "added":
            report.edges_added += 1
        elif outcome in ("cycle", "self"):
            report.edges_skipped.append(f"{label}:{edge_type}:{outcome}")

    # Course overlay: skills with their importance, topics at the default.
    overlay = dict(importance)
    for topic_id in topic_ids.values():
        overlay.setdefault(topic_id, policy.default_importance)
    for skill_id, weight in overlay.items():
        conn.execute(
            """
            insert into public.course_skills
                (course_id, skill_id, importance, source, active, graph_version)
            values (%s, %s, %s, 'COURSE_BOOTSTRAP', true, %s)
            on conflict (course_id, skill_id) do update
               set importance = excluded.importance, active = true,
                   graph_version = excluded.graph_version
            """,
            (course_id, skill_id, weight, report.graph_version),
        )
    conn.execute(
        """
        update public.course_skills set active = false
         where course_id = %s and graph_version < %s and active
        """,
        (course_id, report.graph_version),
    )
    report.course_skill_ids = list(overlay)

    conn.execute(
        """
        update public.courses
           set graph_version = %s, graph_status = 'EMBEDDING', graph_model_run_id = %s,
               graph_error = null
         where id = %s
        """,
        (report.graph_version, model_run_id, course_id),
    )
    return report


def rename_skill(conn: Connection, skill_id: UUID, new_name: str) -> None:
    """Rename keeps the UUID; the old name stays resolvable as PREVIOUS_NAME (DB trigger)."""
    conn.execute(
        "update public.skill_nodes set canonical_name = %s, normalized_name = %s where id = %s",
        (new_name, skill_key(new_name), skill_id),
    )


def merge_skill(conn: Connection, source_id: UUID, target_id: UUID) -> None:
    """Merge source into target: aliases and course overlays move, the source UUID stays
    as a MERGED node pointing at the survivor, so history keeps resolving."""
    if source_id == target_id:
        raise ValueError("cannot merge a skill into itself")
    with conn.transaction():
        conn.execute(
            """
            update public.skill_aliases
               set skill_id = %s,
                   alias_kind = case when alias_kind = 'CANONICAL'
                                     then 'MERGED_NAME'::public.skill_alias_kind
                                     else alias_kind end
             where skill_id = %s
            """,
            (target_id, source_id),
        )
        conn.execute(
            """
            insert into public.course_skills
                (course_id, skill_id, importance, source, active, graph_version)
            select course_id, %s, importance, source, active, graph_version
              from public.course_skills where skill_id = %s
            on conflict (course_id, skill_id) do update
               set importance = greatest(public.course_skills.importance, excluded.importance),
                   active = public.course_skills.active or excluded.active
            """,
            (target_id, source_id),
        )
        conn.execute("delete from public.course_skills where skill_id = %s", (source_id,))
        conn.execute("delete from public.skill_embeddings where skill_id = %s", (source_id,))
        conn.execute(
            """
            update public.skill_nodes set status = 'MERGED', merged_into_id = %s where id = %s
            """,
            (target_id, source_id),
        )
