import logging
import re
from typing import Any

import dlt
import lancedb
from bs4 import BeautifulSoup
from dlt.destinations.adapters import lancedb_adapter
from dlt.sources.helpers.rest_client.paginators import PageNumberPaginator
from dlt.sources.rest_api import rest_api_source
from langchain_text_splitters import RecursiveCharacterTextSplitter
from requests import Response

from src.config import settings

logger = logging.getLogger(__name__)


class WordPressPaginator(PageNumberPaginator):
    """WordPress API returns 400 when requesting a page beyond the last one.

    This paginator treats that as end-of-pagination instead of an error.
    """

    def update_state(self, response: Response, data: list[Any] | None = None) -> None:
        if response.status_code == 400:
            self._has_next_page = False
            return
        super().update_state(response, data)


def remove_html_tags(text):
    """Remove HTML tags, JavaScript, and extra spaces from a string."""
    soup = BeautifulSoup(text, "html.parser")

    for script in soup(["script", "iframe"]):
        script.extract()

    cleaned_text = soup.get_text(separator=" ")
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

    return cleaned_text


_DAN_SECTION_SEPARATORS = [
    "Incident Description",
    "Contributing Factors",
    "Lessons Learned",
    "Recommendations",
    "What Went Right",
    "DAN Evaluation",
    "\n\n",
    "\n",
    " ",
    "",
]


def chunk_text(text, chunk_size=None, chunk_overlap=None):
    """Split text into chunks using RecursiveCharacterTextSplitter.

    DAN section headers are used as preferred split points so that
    'Contributing Factors' and 'Lessons Learned' don't bleed across chunks.
    """
    chunk_size = chunk_size or settings.CHUNK_SIZE
    chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_DAN_SECTION_SEPARATORS,
    )
    return text_splitter.split_text(text)


def _make_resource(name: str, path: str) -> Any:
    """Build a resource config for a DAN WordPress API endpoint."""
    return {
        "name": name,
        "endpoint": {
            "path": path,
            "params": {
                "per_page": settings.DAN_PER_PAGE,
            },
            "incremental": {
                "cursor_path": "modified",
                "initial_value": settings.DAN_START_DATE,
                "start_param": "modified_after",
            },
            # WordPress returns 400 when requesting past the last page
            "response_actions": [
                {"status_code": 400, "action": "ignore"},
            ],
        },
    }


def wordpress_rest_api_source():
    """Create a dlt source for DAN WordPress REST API endpoints."""
    return rest_api_source(
        {
            "client": {
                "base_url": settings.DAN_BASE_URL,
                "paginator": WordPressPaginator(
                    base_page=1,
                    page_param="page",
                    total_path=None,
                ),
            },
            "resource_defaults": {
                "primary_key": "id",
                "write_disposition": "merge",
            },
            "resources": [
                _make_resource("dan_health_resources", "dan_health_resources"),
                _make_resource("dan_alert_diver", "dan_alert_diver"),
                _make_resource("dan_diving_incidents", "dan_diving_incidents"),
                _make_resource("dan_diseases_conds", "dan_diseases_conds"),
            ],
        }
    )


_chunk_count = 0


@dlt.transformer()
def dan_articles(article):
    """Transform DAN articles into text chunks for vectorization."""
    global _chunk_count
    title = article.get("title", {}).get("rendered", "unknown")
    clean_content = remove_html_tags(article["content"]["rendered"])
    chunks = chunk_text(clean_content)
    _chunk_count += len(chunks)
    logger.info(
        "Chunked article '%s' -> %d chunks (total: %d)",
        title,
        len(chunks),
        _chunk_count,
    )
    url = article.get("link", "")
    for chunk in chunks:
        yield {"value": chunk, "title": title, "url": url}


def run_pipeline(*args, **kwargs):
    """Run the DAN articles ingestion pipeline into LanceDB."""
    global _chunk_count
    _chunk_count = 0

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    logger.info("Starting DAN ingestion pipeline...")
    logger.info("Base URL: %s", settings.DAN_BASE_URL)

    pipeline = dlt.pipeline(
        pipeline_name="dan_articles",
        destination="lancedb",
        dataset_name="dan_articles",
    )

    data = wordpress_rest_api_source() | dan_articles

    # Use 'replace' disposition: chunks don't have a natural primary key,
    # and 'merge' requires one for LanceDB orphan removal.
    info = pipeline.run(
        lancedb_adapter(data, embed="value"),
        table_name="texts",
        write_disposition="replace",
    )

    logger.info("Pipeline load info: %s", info)

    logger.info("Building FTS index...")
    db = lancedb.connect(settings.LANCEDB_URI)
    dbtable = db.open_table(settings.LANCEDB_TABLE_NAME)
    dbtable.create_fts_index("value", replace=True)

    row_count = dbtable.count_rows()
    logger.info("Done! Table '%s' has %d rows.", settings.LANCEDB_TABLE_NAME, row_count)

    return settings.LANCEDB_TABLE_NAME
