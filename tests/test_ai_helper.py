"""
Tests for the AI helper module (Autograder/ai_helper.py).

Covers:
- Model tier selection and environment variable overrides
- Model pricing lookup
- Anthropic API response parsing
- OpenAI API response parsing and JSON handling
- Error handling for malformed responses
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from Autograder import ai_helper
from tests.fixtures.llm_mocks import (
    MockAnthropicClient,
    MockOpenAIClient,
    make_grading_result,
)


class TestGetModelForTier:
    """Tests for get_model_for_tier() function."""

    def test_returns_default_tier_when_none_specified(self):
        model = ai_helper.get_model_for_tier("anthropic", None)
        assert model == ai_helper.MODEL_CONFIG["anthropic"]["small"]["name"]

    def test_returns_correct_model_for_each_tier(self):
        for tier in ["small", "medium", "large"]:
            model = ai_helper.get_model_for_tier("anthropic", tier)
            assert model == ai_helper.MODEL_CONFIG["anthropic"][tier]["name"]

    def test_case_insensitive_provider_and_tier(self):
        model1 = ai_helper.get_model_for_tier("ANTHROPIC", "MEDIUM")
        model2 = ai_helper.get_model_for_tier("anthropic", "medium")
        assert model1 == model2

    def test_unknown_provider_returns_unknown(self):
        model = ai_helper.get_model_for_tier("unknown_provider", "small")
        assert model == "unknown"

    def test_unknown_tier_falls_back_to_small(self):
        model = ai_helper.get_model_for_tier("anthropic", "extra_large")
        assert model == ai_helper.MODEL_CONFIG["anthropic"]["small"]["name"]

    def test_environment_variable_override(self, monkeypatch):
        custom_model = "my-custom-model-v2"
        monkeypatch.setenv("ANTHROPIC_MODEL_MEDIUM", custom_model)

        model = ai_helper.get_model_for_tier("anthropic", "medium")
        assert model == custom_model

    def test_environment_variable_takes_precedence(self, monkeypatch):
        custom_model = "override-model"
        monkeypatch.setenv("OPENAI_MODEL_LARGE", custom_model)

        model = ai_helper.get_model_for_tier("openai", "large")
        assert model == custom_model
        assert model != ai_helper.MODEL_CONFIG["openai"]["large"]["name"]


class TestGetModelPricing:
    """Tests for get_model_pricing() function."""

    def test_returns_correct_pricing_for_known_model(self):
        input_cost, output_cost = ai_helper.get_model_pricing(
            "anthropic", "claude-haiku-4-5"
        )
        expected = ai_helper.MODEL_CONFIG["anthropic"]["small"]
        assert input_cost == expected["input_cost"]
        assert output_cost == expected["output_cost"]

    def test_returns_zero_for_unknown_provider(self):
        input_cost, output_cost = ai_helper.get_model_pricing(
            "unknown", "some-model"
        )
        assert input_cost == 0.0
        assert output_cost == 0.0

    def test_returns_small_tier_for_unknown_model(self):
        input_cost, output_cost = ai_helper.get_model_pricing(
            "anthropic", "unknown-model"
        )
        expected = ai_helper.MODEL_CONFIG["anthropic"]["small"]
        assert input_cost == expected["input_cost"]
        assert output_cost == expected["output_cost"]

    def test_ollama_pricing_is_free(self):
        input_cost, output_cost = ai_helper.get_model_pricing("ollama", "qwen3:4b")
        assert input_cost == 0.0
        assert output_cost == 0.0


class TestAnthropicHelper:
    """Tests for AI_Helper__Anthropic class."""

    def test_query_ai_returns_text_and_usage(self, monkeypatch):
        response_text = "This is a test response from Claude."
        mock_client = MockAnthropicClient(responses=[response_text])

        # Patch the class-level client
        monkeypatch.setattr(
            ai_helper.AI_Helper__Anthropic, "_client", mock_client
        )

        result, usage = ai_helper.AI_Helper__Anthropic.query_ai(
            message="Test message",
            attachments=[],
        )

        assert result == response_text
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert usage["provider"] == "anthropic"

    def test_query_ai_uses_correct_tier_model(self, monkeypatch):
        mock_client = MockAnthropicClient(responses=["response"])
        monkeypatch.setattr(
            ai_helper.AI_Helper__Anthropic, "_client", mock_client
        )

        ai_helper.AI_Helper__Anthropic.query_ai(
            message="Test",
            attachments=[],
            tier="large",
        )

        # Check that the large model was used
        call_kwargs = mock_client.messages.last_call_kwargs
        expected_model = ai_helper.MODEL_CONFIG["anthropic"]["large"]["name"]
        assert call_kwargs["model"] == expected_model

    def test_query_ai_handles_attachments(self, monkeypatch):
        mock_client = MockAnthropicClient(responses=["response"])
        monkeypatch.setattr(
            ai_helper.AI_Helper__Anthropic, "_client", mock_client
        )

        ai_helper.AI_Helper__Anthropic.query_ai(
            message="Analyze this image",
            attachments=[("png", "base64encodeddata")],
        )

        call_kwargs = mock_client.messages.last_call_kwargs
        messages = call_kwargs["messages"]
        assert len(messages) == 1
        # Should have text + image content
        content = messages[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"

    def test_query_ai_propagates_api_errors(self, monkeypatch):
        mock_client = MockAnthropicClient(error=Exception("API Error"))
        monkeypatch.setattr(
            ai_helper.AI_Helper__Anthropic, "_client", mock_client
        )

        with pytest.raises(Exception, match="API Error"):
            ai_helper.AI_Helper__Anthropic.query_ai(
                message="Test",
                attachments=[],
            )


class TestOpenAIHelper:
    """Tests for AI_Helper__OpenAI class."""

    def test_query_ai_parses_json_response(self, monkeypatch):
        response_data = {"score": 85, "feedback": "Good work"}
        mock_client = MockOpenAIClient(responses=[json.dumps(response_data)])
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        result, usage = ai_helper.AI_Helper__OpenAI.query_ai(
            message="Grade this",
            attachments=[],
        )

        assert result == response_data
        assert result["score"] == 85
        assert usage["provider"] == "openai"

    def test_query_ai_raises_on_malformed_json(self, monkeypatch):
        """
        BUG DOCUMENTATION: The current implementation catches TypeError but
        json.loads raises JSONDecodeError for malformed JSON. This means
        malformed responses are not retried as intended.

        TODO: Fix ai_helper.py to catch json.JSONDecodeError instead of TypeError
        """
        mock_client = MockOpenAIClient(responses=["not valid json"])
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        # Current behavior: JSONDecodeError is raised, not caught
        with pytest.raises(json.JSONDecodeError):
            ai_helper.AI_Helper__OpenAI.query_ai(
                message="Grade this",
                attachments=[],
                max_retries=2,
            )

    def test_query_ai_retries_on_none_content(self, monkeypatch):
        """
        Test that retry logic works when content is None (which raises TypeError).
        This is the case the current implementation actually handles.
        """
        # Create a mock that returns None content first, then valid JSON
        mock_client = MockOpenAIClient(responses=['{"score": 100}'])

        # Override to return None content on first call
        original_create = mock_client._chat.completions.create
        call_count = [0]

        def patched_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Return a response with None content
                from tests.fixtures.llm_mocks import (
                    MockOpenAICompletion,
                    MockOpenAIChoice,
                    MockOpenAIMessage,
                )
                return MockOpenAICompletion(
                    choices=[MockOpenAIChoice(
                        message=MockOpenAIMessage(content=None)
                    )]
                )
            return original_create(**kwargs)

        mock_client._chat.completions.create = patched_create
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        result, usage = ai_helper.AI_Helper__OpenAI.query_ai(
            message="Grade this",
            attachments=[],
            max_retries=2,
        )

        # Should have retried and gotten valid response
        assert call_count[0] == 2
        assert result == {"score": 100}

    def test_query_ai_uses_correct_tier_model(self, monkeypatch):
        mock_client = MockOpenAIClient(responses=['{"ok": true}'])
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        ai_helper.AI_Helper__OpenAI.query_ai(
            message="Test",
            attachments=[],
            tier="medium",
        )

        call_kwargs = mock_client._chat.completions.last_call_kwargs
        expected_model = ai_helper.MODEL_CONFIG["openai"]["medium"]["name"]
        assert call_kwargs["model"] == expected_model

    def test_query_ai_handles_image_attachments(self, monkeypatch):
        mock_client = MockOpenAIClient(responses=['{"analyzed": true}'])
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        ai_helper.AI_Helper__OpenAI.query_ai(
            message="Analyze image",
            attachments=[("png", "base64data")],
        )

        call_kwargs = mock_client._chat.completions.last_call_kwargs
        messages = call_kwargs["messages"]
        content = messages[0]["content"]
        assert len(content) == 2
        assert content[1]["type"] == "image_url"


class TestGradingResultIntegration:
    """Tests for using AI helpers with grading-specific responses."""

    def test_anthropic_grading_response_parsing(self, monkeypatch):
        grading_result = make_grading_result(
            student_id=12345,
            total_grade=9,
            feedback="Excellent submission!",
        )
        mock_client = MockAnthropicClient(
            responses=[json.dumps(grading_result)]
        )
        monkeypatch.setattr(
            ai_helper.AI_Helper__Anthropic, "_client", mock_client
        )

        result, _ = ai_helper.AI_Helper__Anthropic.query_ai(
            message="Grade this submission",
            attachments=[],
        )

        # Anthropic returns raw text, caller must parse JSON
        parsed = json.loads(result)
        assert parsed["student_id"] == 12345
        assert parsed["total_grade"] == 9

    def test_openai_grading_response_parsing(self, monkeypatch):
        grading_result = make_grading_result(
            student_id=67890,
            total_grade=7,
            feedback="Needs improvement",
        )
        mock_client = MockOpenAIClient(
            responses=[json.dumps(grading_result)]
        )
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        result, _ = ai_helper.AI_Helper__OpenAI.query_ai(
            message="Grade this submission",
            attachments=[],
        )

        # OpenAI helper parses JSON automatically
        assert result["student_id"] == 67890
        assert result["total_grade"] == 7


class TestUsageTracking:
    """Tests for token usage tracking."""

    def test_anthropic_returns_usage_info(self, monkeypatch):
        mock_client = MockAnthropicClient(responses=["response"])
        monkeypatch.setattr(
            ai_helper.AI_Helper__Anthropic, "_client", mock_client
        )

        _, usage = ai_helper.AI_Helper__Anthropic.query_ai(
            message="Test",
            attachments=[],
        )

        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["provider"] == "anthropic"
        assert "model" in usage

    def test_openai_returns_usage_info(self, monkeypatch):
        mock_client = MockOpenAIClient(responses=['{"ok": true}'])
        monkeypatch.setattr(
            ai_helper.AI_Helper__OpenAI, "_client", mock_client
        )

        _, usage = ai_helper.AI_Helper__OpenAI.query_ai(
            message="Test",
            attachments=[],
        )

        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["provider"] == "openai"
