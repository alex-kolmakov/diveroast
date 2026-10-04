import html
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import lancedb
import pandas as pd
from lancedb.rerankers import CrossEncoderReranker

from src.config import settings
from src.observability import get_tracer

_RERANKER: CrossEncoderReranker | None = None

# DAN's case summaries: real divers, what they did, what happened. 139 of
# the ~1,900 articles; unfiltered, general articles outrank them.
INCIDENTS = "url LIKE '%/case-summaries/%'"

NO_GUIDANCE = (
    "No relevant DAN guidance found for this query. Do not cite DAN on this "
    "point; say that no matching DAN material was found."
)


@dataclass
class Retrieval:
    """Retrieved DAN chunks, formatted for the model, plus their sources."""

    text: str
    sources: list[dict[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return self.text


def _get_reranker() -> CrossEncoderReranker | None:
    """Return a cached CrossEncoderReranker, or None if reranking is disabled."""
    global _RERANKER
    if not settings.ENABLE_RERANKING:
        return None
    if _RERANKER is None:
        _RERANKER = CrossEncoderReranker(
            model_name=settings.CROSS_ENCODER_MODEL,
            column="value",  # CRITICAL: LanceDB column is "value" not default "text"
        )
    return _RERANKER


def hybrid_search(
    dbtable, query: str, top_k: int | None = None, where: str | None = None
) -> pd.DataFrame:
    """Perform hybrid search (semantic + FTS) on a LanceDB table.

    Returns up to top_k rows, best first. With reranking on, rows scoring
    below ``RAG_MIN_RELEVANCE`` (a cross-encoder logit) are dropped, so the
    result can be empty. Without reranking the score is a rank-fusion score
    that says nothing about absolute relevance, so no floor is applied.
    ``where`` restricts the search to matching rows first (e.g. INCIDENTS).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "rag.hybrid_search",
        attributes={
            "openinference.span.kind": "RETRIEVER",
            "rag.reranking_enabled": settings.ENABLE_RERANKING,
        },
    ) as span:
        top_k = top_k or settings.RAG_TOP_K
        reranker = _get_reranker()
        search = dbtable.search(query, query_type="hybrid")
        if where:
            search = search.where(where, prefilter=True)
        if reranker is not None:
            query_results = search.rerank(reranker=reranker).to_pandas()
            query_results = query_results[
                query_results["_relevance_score"] >= settings.RAG_MIN_RELEVANCE
            ]
        else:
            query_results = search.to_pandas()
        results = query_results.nlargest(top_k, "_relevance_score")
        span.set_attribute("rag.results_kept", len(results))
        return results


def format_results(results: pd.DataFrame) -> Retrieval:
    """Render search rows as cited chunks and a de-duplicated source list."""
    if results.empty:
        return Retrieval(text=NO_GUIDANCE)
    chunks: list[str] = []
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, row in results.iterrows():
        # WordPress titles arrive HTML-escaped ("Can&#8217;t")
        title = html.unescape(str(row.get("title") or "")).strip()
        url = str(row.get("url") or "").strip()
        header = f"[Source: {title or 'DAN'}]({url})" if url else "[Source: DAN]"
        chunks.append(f"{header}\n{row['value']}")
        if url and url not in seen:
            seen.add(url)
            sources.append({"title": title or url, "url": url})
    return Retrieval(text="\n\n".join(chunks), sources=sources)


def retrieve(
    query: str, top_k: int | None = None, where: str | None = None
) -> Retrieval:
    """Retrieve cited context from the default LanceDB table."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "rag.retrieve_context",
        attributes={"openinference.span.kind": "RETRIEVER"},
    ):
        db = lancedb.connect(settings.LANCEDB_URI)
        dbtable = db.open_table(settings.LANCEDB_TABLE_NAME)
        return format_results(hybrid_search(dbtable, query, top_k, where))


def retrieve_many(
    queries: list[str], top_k: int | None = None, where: str | None = None
) -> Retrieval:
    """Retrieve for several queries, ``top_k`` chunks each, every chunk once.

    Separate searches keep each query focused: joined into one string, the
    phrases dilute each other and the same generic chunks win every time.
    """
    db = lancedb.connect(settings.LANCEDB_URI)
    dbtable = db.open_table(settings.LANCEDB_TABLE_NAME)
    queries = [query for query in queries if query]
    # Reranking dominates and runs outside the GIL, so the searches overlap.
    with ThreadPoolExecutor(max_workers=max(len(queries), 1)) as pool:
        frames = list(
            pool.map(lambda q: hybrid_search(dbtable, q, top_k, where), queries)
        )
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return Retrieval(text=NO_GUIDANCE)
    return format_results(pd.concat(frames).drop_duplicates(subset="value"))


def retrieve_context(query: str, top_k: int | None = None) -> str:
    """Retrieve cited context as plain text."""
    return retrieve(query, top_k).text
