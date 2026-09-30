"""DAN (Divers Alert Network) retrieval tools."""

from src.rag.search import Retrieval, retrieve


def search_dan_incidents(query: str) -> Retrieval:
    """Search DAN content for incident reports matching the query.

    Same index as ``search_dan_guidelines``. The query is sent unprefixed:
    a shared prefix like "diving incident:" matches every DAN document
    lexically and flattens the full-text half of hybrid search.
    """
    return retrieve(query)


def search_dan_guidelines(query: str) -> Retrieval:
    """Search DAN content for safety guidelines matching the query."""
    return retrieve(query)
