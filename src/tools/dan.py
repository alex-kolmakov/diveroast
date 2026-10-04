"""DAN (Divers Alert Network) retrieval tools."""

from src.rag.search import INCIDENTS, Retrieval, retrieve, retrieve_many


def search_dan_incidents(query: str) -> Retrieval:
    """Search DAN's incident case summaries only.

    The query is sent unprefixed: a shared prefix like "diving incident:"
    matches every DAN document lexically and flattens the full-text half of
    hybrid search.
    """
    return retrieve(query, where=INCIDENTS)


def search_dan(
    queries: list[str], top_k: int | None = None, incidents_only: bool = False
) -> Retrieval:
    """Search DAN content for several queries at once, each chunk listed once."""
    return retrieve_many(queries, top_k, where=INCIDENTS if incidents_only else None)


def search_dan_guidelines(query: str) -> Retrieval:
    """Search DAN content for safety guidelines matching the query."""
    return retrieve(query)
