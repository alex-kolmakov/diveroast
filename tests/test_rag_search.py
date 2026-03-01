from unittest.mock import MagicMock, patch

import pandas as pd

import src.rag.search as rag_search_module
from src.rag.search import create_text_report, hybrid_search


def test_hybrid_search():
    mock_table = MagicMock()
    mock_results = pd.DataFrame(
        {
            "value": [
                "DAN incident report about rapid ascent",
                "Safety guidelines for deep diving",
                "Decompression sickness overview",
            ],
            "_relevance_score": [0.9, 0.8, 0.7],
        }
    )
    # Reranker is enabled by default — mock the full chain including .rerank()
    mock_table.search.return_value.rerank.return_value.to_pandas.return_value = (
        mock_results
    )

    context = hybrid_search(mock_table, "diving safety", top_k=2)

    mock_table.search.assert_called_once_with("diving safety", query_type="hybrid")
    assert "DAN incident report about rapid ascent" in context
    assert "Safety guidelines for deep diving" in context


def test_hybrid_search_top_k():
    mock_table = MagicMock()
    mock_results = pd.DataFrame(
        {
            "value": ["result1", "result2", "result3"],
            "_relevance_score": [0.9, 0.8, 0.7],
        }
    )
    mock_table.search.return_value.rerank.return_value.to_pandas.return_value = (
        mock_results
    )

    context = hybrid_search(mock_table, "query", top_k=1)
    assert "result1" in context


def test_hybrid_search_reranking_disabled():
    """When ENABLE_RERANKING=False, .rerank() must not be called."""
    mock_table = MagicMock()
    mock_results = pd.DataFrame(
        {
            "value": ["result_no_rerank"],
            "_relevance_score": [0.95],
        }
    )
    mock_table.search.return_value.to_pandas.return_value = mock_results

    with (
        patch.object(rag_search_module.settings, "ENABLE_RERANKING", False),
        patch.object(rag_search_module, "_RERANKER", None),
    ):
        context = hybrid_search(mock_table, "buoyancy", top_k=1)

    mock_table.search.return_value.rerank.assert_not_called()
    assert "result_no_rerank" in context


def test_create_text_report():
    report = {
        "avg_depth": 15.8,
        "max_depth": 20,
        "depth_variability": 7.4,
        "sac_rate": 15,
        "high_ascend_speed_count": 1,
        "max_ascend_speed": 13,
        "min_ndl": 14,
    }
    text = create_text_report(report)
    assert "15.8" in text
    assert "20" in text
    assert "SAC rate 15" in text
    assert "Minimal NDL 14 minutes" in text


def test_create_text_report_missing_keys():
    report = {"avg_depth": 10.0}
    text = create_text_report(report)
    assert "10.0" in text
    assert "N/A" in text
