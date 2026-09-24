"""Skill embeddings for semantic retrieval (architecture §9.3, §14.3).

A skill is embedded as canonical name + description + aliases. Each stored
vector records the model, the node version and a hash of the exact embedded
text, so an unchanged node is never re-embedded and a renamed node or a newly
added alias is.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from app.model_gateway import EMBEDDING_DIMENSION, ModelGateway, RunContext, sha256_hex

TASK_TYPE = "EMBED_SKILLS"
INPUT_VERSION = "skill-embedding-text/v1"


@dataclass(frozen=True)
class SkillText:
    skill_id: UUID
    version: int
    title: str
    text: str


@dataclass(frozen=True)
class EmbedReport:
    embedded: int
    cached: int


def skill_embedding_text(name: str, description: str, aliases: list[str]) -> tuple[str, str]:
    """(title, text) for a skill document. Aliases are sorted so order never matters."""
    text = f"{name}. {description.strip()}"
    unique_aliases = sorted({a.strip() for a in aliases if a.strip() and a.strip() != name})
    if unique_aliases:
        text += f" Also known as: {', '.join(unique_aliases)}."
    return name, text


def embedding_content_hash(model: str, title: str, text: str) -> str:
    return sha256_hex(
        json.dumps([INPUT_VERSION, model, EMBEDDING_DIMENSION, title, text], ensure_ascii=False)
    )


def vector_literal(vector: list[float]) -> str:
    """pgvector text form, e.g. '[0.1,0.2]'. Cast with ::extensions.vector."""
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


def load_skill_texts(conn: Connection, skill_ids: list[UUID]) -> list[SkillText]:
    rows = conn.execute(
        """
        select n.id, n.version, n.canonical_name, n.description,
               coalesce(array_agg(a.alias order by a.alias)
                        filter (where a.alias_kind <> 'CANONICAL'), '{}')
          from public.skill_nodes n
          left join public.skill_aliases a on a.skill_id = n.id
         where n.id = any(%s) and n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')
         group by n.id
         order by n.canonical_name
        """,
        (skill_ids,),
    ).fetchall()
    texts = []
    for skill_id, version, name, description, aliases in rows:
        title, text = skill_embedding_text(name, description, list(aliases))
        texts.append(SkillText(skill_id, version, title, text))
    return texts


def embed_skills(
    conn: Connection, gateway: ModelGateway, skill_ids: list[UUID], context: RunContext
) -> EmbedReport:
    """Embed ACTIVE assessable skills whose current text has no stored vector."""
    texts = load_skill_texts(conn, skill_ids)
    if not texts:
        return EmbedReport(embedded=0, cached=0)
    model = gateway.embedding_model
    stored = dict(
        conn.execute(
            "select skill_id, content_hash from public.skill_embeddings "
            "where model = %s and skill_id = any(%s)",
            (model, [t.skill_id for t in texts]),
        ).fetchall()
    )
    todo = [
        (t, h)
        for t in texts
        if stored.get(t.skill_id) != (h := embedding_content_hash(model, t.title, t.text))
    ]
    if not todo:
        return EmbedReport(embedded=0, cached=len(texts))

    result = gateway.embed(
        texts=[t.text for t, _ in todo],
        titles=[t.title for t, _ in todo],
        input_type="document",
        prompt_version=INPUT_VERSION,
        task_type=TASK_TYPE,
        context=context,
    )
    with conn.transaction():
        for (text, content_hash), vector, run_id in zip(
            todo, result.vectors, result.vector_run_ids, strict=True
        ):
            conn.execute(
                """
                insert into public.skill_embeddings
                    (skill_id, model, graph_version, content_hash, input_version, embedding,
                     model_run_id)
                values (%s, %s, %s, %s, %s, %s::extensions.vector, %s)
                on conflict (skill_id, model) do update
                   set graph_version = excluded.graph_version,
                       content_hash = excluded.content_hash,
                       input_version = excluded.input_version,
                       embedding = excluded.embedding,
                       model_run_id = excluded.model_run_id,
                       created_at = now()
                """,
                (
                    text.skill_id,
                    result.model,
                    text.version,
                    content_hash,
                    INPUT_VERSION,
                    vector_literal(vector),
                    run_id,
                ),
            )
    return EmbedReport(embedded=len(todo), cached=len(texts) - len(todo))
