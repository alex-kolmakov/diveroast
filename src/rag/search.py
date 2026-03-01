import lancedb
from lancedb.rerankers import CrossEncoderReranker

from src.config import settings
from src.observability import get_tracer

_RERANKER: CrossEncoderReranker | None = None


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


def hybrid_search(dbtable, query: str, top_k: int | None = None) -> str:
    """Perform hybrid search (semantic + FTS) on a LanceDB table.

    Returns concatenated text of top_k most relevant results.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "rag.hybrid_search",
        attributes={
            "openinference.span.kind": "RETRIEVER",
            "rag.reranking_enabled": settings.ENABLE_RERANKING,
        },
    ):
        top_k = top_k or settings.RAG_TOP_K
        reranker = _get_reranker()
        if reranker is not None:
            query_results = (
                dbtable.search(query, query_type="hybrid")
                .rerank(reranker=reranker)
                .to_pandas()
            )
        else:
            query_results = dbtable.search(query, query_type="hybrid").to_pandas()
        results = query_results.sort_values(
            "_relevance_score", ascending=True
        ).nlargest(top_k, "_relevance_score")
        context = "\n".join(results["value"])
        return context


def search_dan_articles(query: str, top_k: int = 3) -> list[dict]:
    """Search DAN articles and return metadata + snippet for each result."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "rag.search_dan_articles",
        attributes={
            "openinference.span.kind": "RETRIEVER",
            "rag.reranking_enabled": settings.ENABLE_RERANKING,
        },
    ):
        try:
            db = lancedb.connect(settings.LANCEDB_URI)
            dbtable = db.open_table(settings.LANCEDB_TABLE_NAME)
            reranker = _get_reranker()
            if reranker is not None:
                query_results = (
                    dbtable.search(query, query_type="hybrid")
                    .rerank(reranker=reranker)
                    .to_pandas()
                )
            else:
                query_results = dbtable.search(query, query_type="hybrid").to_pandas()
            results = query_results.sort_values(
                "_relevance_score", ascending=True
            ).nlargest(top_k, "_relevance_score")

            has_metadata = "title" in results.columns and "url" in results.columns

            articles = []
            seen_urls = set()
            for _, row in results.iterrows():
                snippet = str(row.get("value", ""))
                # Truncate to first sentence only
                dot_pos = snippet.find(". ")
                if dot_pos > 0:
                    snippet = snippet[: dot_pos + 1]
                elif len(snippet) > 150:
                    snippet = snippet[:147] + "..."

                if has_metadata:
                    url = str(row.get("url", ""))
                    title = str(row.get("title", ""))
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                else:
                    url = ""
                    title = ""

                articles.append({"title": title, "url": url, "snippet": snippet})
            return articles
        except Exception:
            return []


def retrieve_context(query: str, top_k: int | None = None) -> str:
    """Retrieve context from the default LanceDB table using hybrid search."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "rag.retrieve_context",
        attributes={"openinference.span.kind": "RETRIEVER"},
    ):
        db = lancedb.connect(settings.LANCEDB_URI)
        dbtable = db.open_table(settings.LANCEDB_TABLE_NAME)
        return hybrid_search(dbtable, query, top_k)


def create_text_report(report: dict) -> str:
    """Convert dive feature data into a natural language description."""
    return (
        f"Average depth {report.get('avg_depth', 'N/A')} meters, "
        f"Maximum depth {report.get('max_depth', 'N/A')} meters, "
        f"Depth variability {report.get('depth_variability', 'N/A')} meters, "
        f"SAC rate {report.get('sac_rate', 'N/A')}, "
        f"High Speed Ascend instances {report.get('high_ascend_speed_count', 'N/A')}, "
        f"Max Ascend Speed {report.get('max_ascend_speed', 'N/A')} meters per min, "
        f"Minimal NDL {report.get('min_ndl', 'N/A')} minutes."
    )
