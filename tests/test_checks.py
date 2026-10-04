"""The answer guard: numbers must come from the data the model was given."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.genai import types

from src.agent.checks import DiveFacts, Grounding, check_and_strip, is_grounded
from src.agent.conversation import UNBACKED_ANSWER, DiverRoastAgent
from src.agent.system_prompts import _get_local_prompt
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
        "Fix it: ascend at 9 m/min and hold 3 min at 5 m."
    )
    cleaned, report = check_and_strip(text, REAL_FACTS, is_roast=True)
    assert report.ungrounded == ["18.7 m/min"]
    assert "18.7" not in cleaned
    assert "Nura reef: 40.5 m is deep." in cleaned
    assert "Fix it: ascend at 9 m/min" in cleaned  # advice isn't checked


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
    assert "no Fix it line" in report.format_issues


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
    prompt = _get_local_prompt()
    with patch.object(agent, "_prior_search", return_value=""):
        answer = agent._run_turn("roast me", prompt)
    assert "97.3" not in answer
    assert f"{depth} m deep" in answer
    assert agent.last_check.ungrounded == ["97.3 m/min"]
    assert "97.3" not in agent.history[-1].parts[0].text  # nor in the history


def test_agent_replaces_a_fully_unbacked_answer():
    agent = _agent_answering("Your 97.3 m/min ascent was wild.")
    with patch.object(agent, "_prior_search", return_value=""):
        assert agent._run_turn("roast me", _get_local_prompt()) == UNBACKED_ANSWER


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
