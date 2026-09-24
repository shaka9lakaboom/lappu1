"""Two-stage, two-channel candidate retrieval (architecture §9.3).

Stage 1 searches the active course overlay, stage 2 the rest of the global
registry. Each stage runs a PostgreSQL full-text channel and a pgvector
cosine channel (up to `channel_limit` each). The union is then scored exactly
(both channels for every pooled skill) and cut to the top `pool_size`; a
structured rerank picks the top `rerank_size` handed to the mapper.

Only ACTIVE, assessable (SKILL/SUBSKILL) nodes are ever candidates.
"""

from uuid import UUID

from psycopg import Connection

from app.intelligence.contracts import RetrievalResult, SkillCandidate
from app.intelligence.policy import RetrievalPolicy
from app.intelligence.retrieval.rerank import rerank_candidates
from app.intelligence.retrieval.scoring import RawCandidate, ScoredCandidate, score_candidates
from app.intelligence.skill_graph.embedding import vector_literal
from app.model_gateway import ModelGateway, RunContext

QUERY_TASK_TYPE = "EMBED_QUERY"
QUERY_INPUT_VERSION = "retrieval-query/v1"

# q.tsq ORs the query's English lexemes: a long segment must not need every word to match.
_CANDIDATE_SQL = """
with q as (select replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery as tsq),
scope as (
    select cs.skill_id, max(cs.importance) as importance
      from public.course_skills cs
     where cs.course_id = any(%(courses)s) and cs.active
     group by cs.skill_id
),
live as (
    select n.id, n.search_document from public.skill_nodes n
     where n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')
),
lex_course as (
    select l.id from live l join scope s on s.skill_id = l.id, q
     where l.search_document @@ q.tsq
     order by ts_rank_cd(l.search_document, q.tsq) desc, l.id limit %(k)s
),
lex_global as (
    select l.id from live l, q
     where l.search_document @@ q.tsq and not exists (select 1 from scope s where s.skill_id = l.id)
     order by ts_rank_cd(l.search_document, q.tsq) desc, l.id limit %(k)s
),
vec_course as (
    select e.skill_id as id from public.skill_embeddings e
      join scope s on s.skill_id = e.skill_id
      join live l on l.id = e.skill_id
     where e.model = %(model)s
     order by e.embedding operator(extensions.<=>) %(vec)s::extensions.vector, e.skill_id
     limit %(k)s
),
vec_global as (
    select e.skill_id as id from public.skill_embeddings e
      join live l on l.id = e.skill_id
     where e.model = %(model)s
       and not exists (select 1 from scope s where s.skill_id = e.skill_id)
     order by e.embedding operator(extensions.<=>) %(vec)s::extensions.vector, e.skill_id
     limit %(k)s
),
pool as (
    select id from lex_course union select id from lex_global
    union select id from vec_course union select id from vec_global
)
select n.id, n.canonical_name, n.description, n.node_kind::text,
       ts_rank_cd(n.search_document, q.tsq) as lexical_raw,
       case when e.embedding is null then null
            else 1 - (e.embedding operator(extensions.<=>) %(vec)s::extensions.vector) end,
       s.importance,
       coalesce((select array_agg(pe.from_skill_id order by pe.from_skill_id)
                   from public.skill_edges pe join public.skill_nodes pn on pn.id = pe.from_skill_id
                  where pe.to_skill_id = n.id and pe.edge_type = 'PARENT'
                    and pn.node_kind in ('SKILL', 'SUBSKILL')), '{}'),
       coalesce((select array_agg(pn.canonical_name order by pn.canonical_name)
                   from public.skill_edges pe join public.skill_nodes pn on pn.id = pe.from_skill_id
                  where pe.to_skill_id = n.id and pe.edge_type = 'PARENT'
                    and pn.node_kind in ('DOMAIN', 'SUBJECT', 'TOPIC')), '{}')
  from pool p
  join public.skill_nodes n on n.id = p.id
  cross join q
  left join public.skill_embeddings e on e.skill_id = n.id and e.model = %(model)s
  left join scope s on s.skill_id = n.id
"""


def fetch_raw_candidates(
    conn: Connection,
    *,
    query_text: str,
    query_vector: list[float] | None,
    course_ids: list[UUID],
    embedding_model: str,
    channel_limit: int,
) -> list[RawCandidate]:
    params = {
        "text": query_text,
        "courses": course_ids,
        "model": embedding_model,
        # With no query vector the semantic channel matches nothing (model filter).
        "vec": vector_literal(query_vector) if query_vector else None,
        "k": channel_limit,
    }
    sql = _CANDIDATE_SQL
    if query_vector is None:
        sql = sql.replace("where e.model = %(model)s", "where false")
    rows = conn.execute(sql, params).fetchall()
    return [
        RawCandidate(
            skill_id=r[0],
            canonical_name=r[1],
            description=r[2],
            node_kind=r[3],
            lexical_raw=float(r[4] or 0.0),
            semantic=float(r[5]) if r[5] is not None else None,
            course_importance=float(r[6]) if r[6] is not None else None,
            parent_ids=tuple(r[7]),
            topic_names=tuple(r[8]),
        )
        for r in rows
    ]


def to_contract(scored: list[ScoredCandidate]) -> list[SkillCandidate]:
    return [
        SkillCandidate(
            skill_id=s.raw.skill_id,
            canonical_name=s.raw.canonical_name,
            description=s.raw.description,
            node_kind=s.raw.node_kind,  # type: ignore[arg-type]
            in_course=s.in_course,
            parent_ids=s.raw.parent_ids,
            topic_names=s.raw.topic_names,
            semantic_similarity=s.semantic_similarity,
            lexical_score=s.lexical_score,
            course_context_prior=s.course_context_prior,
            candidate_score=s.candidate_score,
            rank=index + 1,
        )
        for index, s in enumerate(scored)
    ]


def retrieve_candidates(
    conn: Connection,
    gateway: ModelGateway,
    *,
    query_text: str,
    course_ids: list[UUID],
    course_context: str,
    policy: RetrievalPolicy,
    context: RunContext,
) -> RetrievalResult:
    """Embed the query, gather and score the top-N pool, rerank to the top-K."""
    text = query_text[: policy.query_max_chars]
    embedded = gateway.embed(
        texts=[text],
        input_type="query",
        prompt_version=QUERY_INPUT_VERSION,
        task_type=QUERY_TASK_TYPE,
        context=context,
    )
    raw = fetch_raw_candidates(
        conn,
        query_text=text,
        query_vector=embedded.vectors[0],
        course_ids=course_ids,
        embedding_model=embedded.model,
        channel_limit=policy.channel_limit,
    )
    pool = to_contract(score_candidates(raw, policy.weights, policy.pool_size))
    reranked, rerank_run_id, fallback = rerank_candidates(
        gateway,
        segment_text=text,
        course_context=course_context,
        candidates=pool,
        rerank_size=policy.rerank_size,
        context=context,
    )
    return RetrievalResult(
        course_ids=tuple(course_ids),
        candidates=tuple(pool),
        reranked_ids=tuple(reranked),
        rerank_fallback=fallback,
        query_model_run_id=embedded.runs[-1].id,
        rerank_model_run_id=rerank_run_id,
    )
