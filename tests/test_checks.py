"""The answer guard: numbers must come from the data the model was given."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.genai import types

from src.agent.checks import DiveFacts, Grounding, check_and_strip, is_grounded
from src.agent.conversation import UNBACKED_ANSWER, DiverRoastAgent
from src.agent.system_prompts import LOCAL_PROMPT
from src.parsers import get_parser

FULL = "tests/fixtures/anonymized_subsurface_export.ssrf"
# Roasts the real model wrote on 2026-10-04 for a 196-dive log; every number
# in them is real. Their log isn't in the repo, so they're checked against
# the facts they quote.
REAL = json.loads(Path("tests/fixtures/real_roasts.json").read_text())


def _grounding(*dives: tuple[list[str], str], general: str = "") -> Grounding:
    many = [DiveFacts([f"filler {i}"], "x") for i in range(20)]  # a big log
    return Grounding(
        general=general, dives=[DiveFacts(n, line) for n, line in dives] + many
    )


REAL_FACTS = _grounding(
    (["ProDivingClub"], "surfacing 23.4m/min, SAC 59.0L/min"),
    (["North red sea"], "surfacing 16.2m/min"),
    (["Seven sisters"], "depth 37.5m, NDL 0min, SAC 21.8L/min"),
    (["Nura reef"], "depth 40.5m"),
)


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_roasts_pass_untouched(name):
    cleaned, report = check_and_strip(REAL[name], REAL_FACTS, is_roast=True)
    assert report.ungrounded == []
    assert cleaned == REAL[name].strip()


def test_invented_number_is_stripped_with_its_sentence():
    text = (
        "- Nura reef: 40.5 m is deep. Your 18.7 m/min ascent there was worse.\n"
        "- North red sea: 16.2 m/min surfacing.\n\n"
        "Ascend at 9 m/min and hold 3 min at 5 m."
    )
    cleaned, report = check_and_strip(text, REAL_FACTS, is_roast=True)
    assert report.ungrounded == ["18.7 m/min"]
    assert "18.7" not in cleaned
    assert "Nura reef: 40.5 m is deep." in cleaned
    assert "Ascend at 9 m/min" in cleaned  # diving constants count as grounded


def test_number_from_another_dive_does_not_count():
    """23.4 m/min is real, but it's ProDivingClub's, not Nura reef's."""
    _, report = check_and_strip("Nura reef: 23.4 m/min up.", REAL_FACTS, is_roast=False)
    assert report.ungrounded == ["23.4 m/min"]


def test_bullet_with_nothing_left_is_removed():
    text = "Verdict: fast.\n- Nura reef: 99.9 m/min.\n- North red sea: 16.2 m/min."
    cleaned, _ = check_and_strip(text, REAL_FACTS, is_roast=False)
    assert cleaned == "Verdict: fast.\n- North red sea: 16.2 m/min."


def test_rounding_is_allowed():
    assert is_grounded("16.2", [16.24])
    assert is_grounded("38", [37.5])
    assert not is_grounded("16.3", [16.24])


def test_soft_checks_report_without_changing_the_text():
    text = "- North red sea: 16.2 m/min, a rocket. DAN would weep."
    cleaned, report = check_and_strip(text, REAL_FACTS, is_roast=True)
    assert cleaned == text
    assert report.banned == ["rocket"]
    assert report.dan_without_link
    assert report.format_issues == ["1 list lines"]


# --- The guard in the agent ---------------------------------------------------


def _agent_answering(text: str) -> DiverRoastAgent:
    df = get_parser(FULL).parse(FULL)
    agent = DiverRoastAgent()
    agent.set_dive_data(df)
    client = MagicMock()
    client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)])
            )
        ]
    )
    agent._client = client
    return agent


def _site_and_depth(agent: DiverRoastAgent) -> tuple[str, float]:
    row = agent.features.dropna(subset=["max_depth"]).iloc[0]
    return str(row["dive_site_name"]), round(float(row["max_depth"]), 1)


def test_agent_strips_an_invented_number_before_anyone_sees_it():
    agent = _agent_answering("")
    site, depth = _site_and_depth(agent)
    text = f"- {site}: {depth} m deep. You hit 97.3 m/min on the way up there."
    agent._client = _agent_answering(text)._client
    prompt = LOCAL_PROMPT
    with patch.object(agent, "_prior_search", return_value=""):
        answer = agent._run_turn("roast me", prompt)
    assert "97.3" not in answer
    assert f"{depth} m deep" in answer
    assert agent.last_check.ungrounded == ["97.3 m/min"]
    assert "97.3" not in agent.history[-1].parts[0].text  # nor in the history


def test_agent_replaces_a_fully_unbacked_answer():
    agent = _agent_answering("Your 97.3 m/min ascent was wild.")
    with patch.object(agent, "_prior_search", return_value=""):
        assert agent._run_turn("roast me", LOCAL_PROMPT) == UNBACKED_ANSWER


# --- DAN links the model misused --------------------------------------------


SOURCES = [
    {
        "title": "Ascent Rates",
        "url": "https://dan.org/alert-diver/article/ascent-rates/",
    },
    {
        "title": "Diving with an Infectious Disease",
        "url": "https://dan.org/infectious/",
    },
]


def test_link_named_after_an_article_becomes_a_citation():
    from src.agent.conversation import repair_dan_links

    text = (
        "Slow down ([Ascent Rates](https://dan.org/alert-diver/article/ascent-rates/))."
    )
    assert repair_dan_links(text, SOURCES) == (
        "Slow down ([DAN: Ascent Rates](https://dan.org/alert-diver/article/ascent-rates/))."
    )


def test_site_name_linked_to_dan_loses_the_link():
    """Seen 2026-10-04: site names linked to unrelated DAN articles."""
    from src.agent.conversation import repair_dan_links

    text = (
        "Deco on [Seven sisters, Marker 39 (Red sea, Jun 2024)]"
        "(https://dan.org/infectious/), nine times."
    )
    assert repair_dan_links(text, SOURCES) == (
        "Deco on Seven sisters, Marker 39 (Red sea, Jun 2024), nine times."
    )


def test_proper_citations_and_other_links_are_untouched():
    from src.agent.conversation import repair_dan_links

    text = (
        "Fast ([DAN: Ascent Rates](https://dan.org/alert-diver/article/ascent-rates/)); "
        "map [here](https://www.openstreetmap.org/x)."
    )
    assert repair_dan_links(text, SOURCES) == text


def test_dive_without_a_site_is_not_called_unknown():
    """Seen 2026-10-04: a FIT roast said "at unknown" three times."""
    fit = "tests/fixtures/garmin_descent_scuba.fit"
    agent = DiverRoastAgent()
    agent.set_dive_data(get_parser(fit).parse(fit))
    seed = agent.history[0].parts[0].text
    assert "unknown:" not in seed
    assert "(no site name recorded)" in seed


# --- Incident reports in the DAN material -----------------------------------


def test_incident_cases_get_their_own_section(monkeypatch):
    from src.rag.search import Retrieval

    agent = _agent_answering("ok")
    guidance = Retrieval(
        text="[Source: Ascent Rates](https://dan.org/a)\\nGo slow.",
        sources=[{"title": "Ascent Rates", "url": "https://dan.org/a"}],
    )
    case = Retrieval(
        text="[Source: Runaway Ascent](https://dan.org/case-summaries/r)\\nHe bolted.",
        sources=[
            {"title": "Runaway Ascent", "url": "https://dan.org/case-summaries/r"}
        ],
    )

    def search(queries, top_k=None, incidents_only=False):
        return case if incidents_only else guidance

    with patch("src.agent.conversation.dan.search_dan", side_effect=search):
        material = agent._prior_search("roast me")
    assert "Go slow." in material
    assert "DAN incident reports" in material and "He bolted." in material
    assert [s["title"] for s in agent.last_sources] == [
        "Ascent Rates",
        "Runaway Ascent",
    ]


def test_failed_incident_search_keeps_the_guidance():
    from src.rag.search import Retrieval

    agent = _agent_answering("ok")
    guidance = Retrieval(
        text="[Source: Ascent Rates](https://dan.org/a)\\nGo slow.",
        sources=[{"title": "Ascent Rates", "url": "https://dan.org/a"}],
    )

    def search(queries, top_k=None, incidents_only=False):
        if incidents_only:
            raise RuntimeError("filter failed")
        return guidance

    with patch("src.agent.conversation.dan.search_dan", side_effect=search):
        material = agent._prior_search("roast me")
    assert "Go slow." in material and "incident reports" not in material


def test_roasts_report_list_lines_and_length():
    from src.agent.checks import format_issues

    listy = "Fast diver.\n- Nura reef: fast.\n- Blue Hole: deep."
    prose = "Fast diver, always. Nura reef proves it."
    assert format_issues(listy, is_roast=True) == ["2 list lines"]
    assert format_issues(prose, is_roast=True) == []
    assert format_issues("word " * 100, is_roast=False) == ["100 words"]


def test_plain_text_source_note_becomes_a_citation():
    """Seen 2026-10-04: an incident used as "(Source: Title)", unlinked."""
    from src.agent.conversation import repair_dan_links

    sources = [
        {"title": "Inflator Malfunction", "url": "https://dan.org/case-summaries/i/"}
    ]
    text = "One diver lost control (Source: Inflator Malfunction), yet you keep at it."
    assert repair_dan_links(text, sources) == (
        "One diver lost control ([DAN: Inflator Malfunction]"
        "(https://dan.org/case-summaries/i/)), yet you keep at it."
    )
    unknown = "Trust me (Source: Something Invented)."
    assert repair_dan_links(unknown, sources) == "Trust me."


def test_a_dive_named_earlier_in_the_paragraph_counts():
    """Seen 2026-10-05: a true sentence stripped because its dive was named
    in the sentence before."""
    text = "Take Nura reef. You hit 40.5 m there and lingered."
    cleaned, report = check_and_strip(text, REAL_FACTS, is_roast=False)
    assert report.ungrounded == [] and cleaned == text
    # ...but not across paragraphs
    split = "Take Nura reef.\n\nYou hit 23.4 m/min there."
    _, report = check_and_strip(split, REAL_FACTS, is_roast=False)
    assert report.ungrounded == ["23.4 m/min"]


# --- Outcomes pinned on DAN ---------------------------------------------------

INFLATOR = "https://dan.org/case-summaries/inflator/"
MISSED_DECO = "https://dan.org/case-summaries/two-missed-deco-alerts/"
CASE_TEXTS = {
    INFLATOR: "A diver ascended rapidly when the BCD inflator fired. The next day "
    "she reported a mild headache, which resolved.",
    MISSED_DECO: "One diver began to exhibit a skin rash related to decompression "
    "sickness and called the DAN hotline.",
}


def _cited(*dives, sources=CASE_TEXTS) -> Grounding:
    return Grounding(general="", dives=list(dives), sources=sources)


def test_invented_injury_cited_to_dan_is_stripped():
    """The locked-in example, 2026-10-05: a mild headache became seizures."""
    text = (
        "You sprinted the last metres like an amateur. I have read this exact "
        "profile in case files where the diver surfaces seizing or coughing blood "
        f"([DAN: Inflator Malfunction]({INFLATOR}))."
    )
    cleaned, report = check_and_strip(text, _cited(), is_roast=False)
    assert report.misattributed == ["lung injury", "neurological harm"]
    assert cleaned == "You sprinted the last metres like an amateur."


def test_outcome_the_article_does_say_is_kept_in_any_wording():
    text = (
        "A buddy pair rode their NDLs the same way and one went home bent "
        f"([DAN: Two Missed Deco Alerts]({MISSED_DECO}))."
    )
    cleaned, report = check_and_strip(text, _cited(), is_roast=False)
    assert report.misattributed == [] and cleaned == text


def test_uncited_figures_of_speech_are_left_alone():
    text = "Keep diving like that and the chamber crew will learn your name."
    cleaned, report = check_and_strip(text, _cited(), is_roast=False)
    assert report.misattributed == [] and cleaned == text


def test_source_texts_are_read_from_the_material_the_model_saw():
    from src.agent.checks import source_texts

    material = (
        f"[Source: Inflator Malfunction]({INFLATOR})\nA mild headache.\n\n"
        f"[Source: Inflator Malfunction]({INFLATOR})\nIt resolved.\n\n"
        f"[Source: Two Missed Deco Alerts]({MISSED_DECO})\nA skin rash."
    )
    texts = source_texts(material)
    assert texts[INFLATOR.rstrip("/")] == "A mild headache.\nIt resolved."
    assert "skin rash" in texts[MISSED_DECO.rstrip("/")]


def test_unnumbered_dives_are_shown_by_date_not_internal_id():
    """Seen 2026-10-05: a roast quoted "#unnum_2025-10-14_154315"."""
    from src.tools.dive import dive_label

    assert dive_label("unnum_2025-10-14_154315") == "2025-10-14 15:43"
    assert dive_label("unnum_2025-10-14") == "2025-10-14"
    assert dive_label("44") == "#44"
