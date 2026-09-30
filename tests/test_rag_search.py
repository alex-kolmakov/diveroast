from unittest.mock import MagicMock, patch

import pandas as pd

import src.rag.search as rag_search_module
from src.rag.search import NO_GUIDANCE, format_results, hybrid_search
from src.tools.dive import describe_features


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

    results = hybrid_search(mock_table, "diving safety", top_k=2)

    mock_table.search.assert_called_once_with("diving safety", query_type="hybrid")
    assert results["value"].tolist() == [
        "DAN incident report about rapid ascent",
        "Safety guidelines for deep diving",
    ]


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

    results = hybrid_search(mock_table, "query", top_k=1)
    assert results["value"].tolist() == ["result1"]


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
        results = hybrid_search(mock_table, "buoyancy", top_k=1)

    mock_table.search.return_value.rerank.assert_not_called()
    assert results["value"].tolist() == ["result_no_rerank"]


def test_relevance_floor_drops_irrelevant_chunks():
    """Reranked rows below RAG_MIN_RELEVANCE are dropped, even if that's all of them."""
    mock_table = MagicMock()
    mock_table.search.return_value.rerank.return_value.to_pandas.return_value = (
        pd.DataFrame({"value": ["pizza", "stocks"], "_relevance_score": [-11.0, -10.9]})
    )
    with patch.object(rag_search_module.settings, "RAG_MIN_RELEVANCE", -5.0):
        results = hybrid_search(mock_table, "best pizza recipe", top_k=3)
    assert results.empty
    assert format_results(results).text == NO_GUIDANCE


def test_format_results_cites_sources():
    results = pd.DataFrame(
        {
            "value": ["Ascend no faster than 9 m/min.", "More on ascents.", "Other"],
            "title": ["Ascent Rates", "Ascent Rates", "Safety Stops"],
            "url": ["https://dan.org/a", "https://dan.org/a", "https://dan.org/b"],
            "_relevance_score": [2.0, 1.0, 0.5],
        }
    )
    retrieval = format_results(results)
    assert "[Source: Ascent Rates](https://dan.org/a)" in retrieval.text
    assert "Ascend no faster than 9 m/min." in retrieval.text
    assert retrieval.sources == [
        {"title": "Ascent Rates", "url": "https://dan.org/a"},
        {"title": "Safety Stops", "url": "https://dan.org/b"},
    ]


def test_describe_features():
    row = {
        "avg_depth": 15.8,
        "max_depth": 20,
        "max_ascend_speed": 13,
        "high_ascend_speed_count": 1,
        "min_ndl": 14,
        "sac_rate": 15,
    }
    text = describe_features(row)
    assert "15.8 m" in text
    assert "SAC rate 15.0 L/min" in text
    assert "minimum NDL 14 min" in text


def test_describe_features_leaves_out_unrecorded_metrics():
    row = {
        "avg_depth": 10.0,
        "max_depth": 12.0,
        "max_ascend_speed": 5.0,
        "high_ascend_speed_count": 0,
        "min_ndl": float("nan"),
        "sac_rate": None,
    }
    text = describe_features(row)
    assert "10.0 m" in text
    assert "NDL" not in text
    assert "SAC" not in text
