"""Tests for system prompt loading with Phoenix fallback."""

from unittest.mock import MagicMock, patch

from src.agent.system_prompts import (
    LOCAL_PROMPT,
    PromptVersion,
    get_active_prompt,
    get_prompt_from_phoenix,
)


class TestGetPromptFromPhoenix:
    """Tests for Phoenix prompt fetching."""

    def test_returns_none_when_phoenix_unreachable(self):
        """When Phoenix client raises a connection error, returns None."""
        with patch(
            "phoenix.client.Client",
            side_effect=Exception("Connection refused"),
        ):
            result = get_prompt_from_phoenix()
            assert result is None

    def test_returns_none_when_prompt_not_found(self):
        """When the prompt doesn't exist in Phoenix, returns None."""
        mock_client = MagicMock()
        mock_client.prompts.get.side_effect = Exception("Prompt not found")
        with patch("phoenix.client.Client", return_value=mock_client):
            result = get_prompt_from_phoenix()
            assert result is None

    def test_returns_prompt_version_on_success(self):
        """When Phoenix returns a valid prompt, returns a PromptVersion."""
        mock_formatted = MagicMock()
        mock_formatted.messages = [
            {"role": "system", "content": "You are DiveRoast, a test prompt."}
        ]
        mock_prompt = MagicMock()
        mock_prompt.id = "phoenix-version-abc123"
        mock_prompt.format.return_value = mock_formatted

        mock_client = MagicMock()
        mock_client.prompts.get.return_value = mock_prompt

        with patch("phoenix.client.Client", return_value=mock_client):
            result = get_prompt_from_phoenix()
            assert result is not None
            assert isinstance(result, PromptVersion)
            assert result.prompt == "You are DiveRoast, a test prompt."
            assert result.phoenix_version_id == "phoenix-version-abc123"
            assert result.label == "phoenix-production"

    def test_returns_none_when_no_system_message(self):
        """When Phoenix prompt has no system message, returns None."""
        mock_formatted = MagicMock()
        mock_formatted.messages = [{"role": "user", "content": "Hello"}]
        mock_prompt = MagicMock()
        mock_prompt.id = "phoenix-version-xyz"
        mock_prompt.format.return_value = mock_formatted

        mock_client = MagicMock()
        mock_client.prompts.get.return_value = mock_prompt

        with patch("phoenix.client.Client", return_value=mock_client):
            result = get_prompt_from_phoenix()
            assert result is None


class TestGetActivePrompt:
    """Tests for get_active_prompt with fallback behavior."""

    def test_falls_back_to_local_when_phoenix_unavailable(self):
        """When Phoenix is down, get_active_prompt returns the local version."""
        with patch(
            "src.agent.system_prompts.get_prompt_from_phoenix",
            return_value=None,
        ):
            result = get_active_prompt()
            assert result == LOCAL_PROMPT
            assert result.phoenix_version_id is None
            assert "DiveRoast" in result.prompt

    def test_uses_phoenix_when_available(self):
        """When Phoenix returns a prompt, get_active_prompt uses it."""
        phoenix_pv = PromptVersion(
            version=0,
            label="phoenix-production",
            prompt="Phoenix system prompt here",
            phoenix_version_id="ver-123",
        )
        with patch(
            "src.agent.system_prompts.get_prompt_from_phoenix",
            return_value=phoenix_pv,
        ):
            result = get_active_prompt()
            assert result.phoenix_version_id == "ver-123"
            assert result.prompt == "Phoenix system prompt here"

    def test_local_prompt_exists(self):
        assert LOCAL_PROMPT.prompt and LOCAL_PROMPT.label and LOCAL_PROMPT.version


def test_prompt_is_prose_with_incidents_and_no_numbers_to_copy():
    import re

    text = LOCAL_PROMPT.prompt
    assert "Prose only" in text and "bullet list" not in text
    assert "Fix it" not in text
    assert "DAN incident reports" in text
    # Examples get copied, and a number in the prompt counts as grounded
    # for the answer guard: no measured-looking values in the prompt.
    assert not re.search(r"\d+\.\d+ ?m/min", text)
