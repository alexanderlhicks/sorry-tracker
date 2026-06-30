"""Tests for the OpenRouter-backed llm_provider.

The provider imports the `openai` SDK lazily inside ``__init__``; these tests
avoid that import by building instances via ``__new__`` for the pure-helper
checks, and by injecting a fake ``openai`` module for the one test that
exercises real client construction.
"""

import importlib.util
import sys
import types
import unittest
from unittest import mock

from pydantic import BaseModel

import llm_provider
from llm_provider import (
    ContentPart,
    OpenRouterProvider,
    TokenUsage,
    _data_url,
    create_provider,
)


class _Schema(BaseModel):
    x: int


def _bare_provider(max_tokens=16384, reasoning_default=None, require_parameters=False):
    """An OpenRouterProvider instance without running __init__ (no openai import)."""
    p = OpenRouterProvider.__new__(OpenRouterProvider)
    p.max_tokens = max_tokens
    p.reasoning_default = reasoning_default
    p.require_parameters = require_parameters
    return p


class _FakeUsage:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


class _FakeMessage:
    def __init__(self, parsed=None, content=None):
        self.parsed = parsed
        self.content = content


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(self, message, usage=None, finish_reason="stop"):
        self.choices = [_FakeChoice(message, finish_reason)]
        self.usage = usage


class DataUrlTests(unittest.TestCase):
    def test_bytes_become_base64_data_url(self):
        url = _data_url(b"hello", "application/pdf")
        self.assertTrue(url.startswith("data:application/pdf;base64,"))

    def test_str_passes_through(self):
        self.assertEqual(_data_url("https://x/y.png", "image/png"), "https://x/y.png")


class MessageContentTests(unittest.TestCase):
    def setUp(self):
        self.p = _bare_provider()

    def test_text_block(self):
        blocks, has_pdf = self.p._to_message_content([ContentPart("text", "hi")])
        self.assertEqual(blocks, [{"type": "text", "text": "hi"}])
        self.assertFalse(has_pdf)

    def test_cached_text_gets_cache_control(self):
        blocks, _ = self.p._to_message_content([ContentPart("text", "ctx", cache=True)])
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})

    def test_image_bytes(self):
        blocks, _ = self.p._to_message_content(
            [ContentPart("image", b"\x89PNG", mime_type="image/png")]
        )
        self.assertEqual(blocks[0]["type"], "image_url")
        self.assertTrue(blocks[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_url_passthrough(self):
        blocks, _ = self.p._to_message_content([ContentPart("image", "https://x/i.png")])
        self.assertEqual(blocks[0]["image_url"]["url"], "https://x/i.png")

    def test_pdf_sets_has_pdf_and_file_block(self):
        blocks, has_pdf = self.p._to_message_content([ContentPart("pdf", b"%PDF-1.4")])
        self.assertTrue(has_pdf)
        self.assertEqual(blocks[0]["type"], "file")
        self.assertEqual(blocks[0]["file"]["filename"], "document.pdf")
        self.assertTrue(blocks[0]["file"]["file_data"].startswith("data:application/pdf;base64,"))
        self.assertNotIn("cache_control", blocks[0])

    def test_cache_control_honored_on_pdf_and_image(self):
        # Regression: a trailing PDF/image reference must still carry the cache
        # breakpoint, not just text parts.
        pdf_blocks, _ = self.p._to_message_content([ContentPart("pdf", b"%PDF", cache=True)])
        self.assertEqual(pdf_blocks[0]["cache_control"], {"type": "ephemeral"})
        img_blocks, _ = self.p._to_message_content([ContentPart("image", b"\x89PNG", cache=True)])
        self.assertEqual(img_blocks[0]["cache_control"], {"type": "ephemeral"})

    def test_unknown_type_skipped(self):
        with self.assertLogs(level="WARNING"):
            blocks, _ = self.p._to_message_content([ContentPart("video", b"x")])
        self.assertEqual(blocks, [])


class ExtraBodyTests(unittest.TestCase):
    def test_require_parameters_off_by_default(self):
        body = _bare_provider()._build_extra_body(None, has_pdf=False)
        self.assertNotIn("provider", body)

    def test_require_parameters_set_when_enabled(self):
        body = _bare_provider(require_parameters=True)._build_extra_body(None, has_pdf=False)
        self.assertTrue(body["provider"]["require_parameters"])

    def test_response_healing_plugin_present(self):
        body = _bare_provider()._build_extra_body(None, has_pdf=False)
        self.assertIn({"id": "response-healing"}, body["plugins"])
        self.assertNotIn("file-parser", [p["id"] for p in body["plugins"]])

    def test_file_parser_plugin_added_for_pdf(self):
        body = _bare_provider()._build_extra_body(None, has_pdf=True)
        ids = [p["id"] for p in body["plugins"]]
        self.assertIn("file-parser", ids)

    def test_reasoning_from_budget(self):
        body = _bare_provider()._build_extra_body(4096, has_pdf=False)
        self.assertEqual(body["reasoning"], {"max_tokens": 4096})

    def test_reasoning_default_used_when_no_budget(self):
        body = _bare_provider(reasoning_default={"effort": "high"})._build_extra_body(None, False)
        self.assertEqual(body["reasoning"], {"effort": "high"})

    def test_no_reasoning_when_none(self):
        body = _bare_provider()._build_extra_body(None, has_pdf=False)
        self.assertNotIn("reasoning", body)

    def test_zero_budget_disables_reasoning(self):
        body = _bare_provider()._build_extra_body(0, has_pdf=False)
        self.assertNotIn("reasoning", body)


class MaxTokensTests(unittest.TestCase):
    def test_default_without_budget(self):
        self.assertEqual(_bare_provider(max_tokens=16384)._max_tokens_for(None), 16384)

    def test_reserves_full_base_above_budget(self):
        # answer headroom = full base, on top of the thinking budget
        self.assertEqual(_bare_provider(max_tokens=16384)._max_tokens_for(20000), 36384)
        self.assertEqual(_bare_provider(max_tokens=16384)._max_tokens_for(10240), 26624)

    def test_zero_budget_is_base(self):
        self.assertEqual(_bare_provider(max_tokens=65536)._max_tokens_for(0), 65536)


class GenerateStructuredTests(unittest.TestCase):
    def _provider_with_parse(self, completion):
        p = _bare_provider()
        parse = mock.Mock(return_value=completion)
        p.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=parse))
        )
        return p, parse

    def test_returns_parsed_and_usage(self):
        usage = _FakeUsage({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 7},
            "prompt_tokens_details": {"cached_tokens": 50},
            "cost": 0.012,
        })
        completion = _FakeCompletion(_FakeMessage(parsed=_Schema(x=5)), usage=usage)
        p, parse = self._provider_with_parse(completion)

        parsed, tokens = p.generate_structured(
            "anthropic/claude-opus-4.8", [ContentPart("text", "hi")], _Schema, thinking_budget=4096
        )

        self.assertEqual(parsed.x, 5)
        self.assertEqual(tokens.input_tokens, 100)
        self.assertEqual(tokens.output_tokens, 20)
        self.assertEqual(tokens.thinking_tokens, 7)
        self.assertEqual(tokens.cached_tokens, 50)
        self.assertAlmostEqual(tokens.cost, 0.012)

        # Wiring: schema passed as response_format, reasoning + provider in extra_body.
        _, kwargs = parse.call_args
        self.assertIs(kwargs["response_format"], _Schema)
        self.assertEqual(kwargs["model"], "anthropic/claude-opus-4.8")
        self.assertEqual(kwargs["extra_body"]["reasoning"], {"max_tokens": 4096})
        self.assertNotIn("provider", kwargs["extra_body"])  # require_parameters off by default
        self.assertEqual(kwargs["messages"][0]["role"], "user")

    def test_falls_back_to_validate_json(self):
        completion = _FakeCompletion(_FakeMessage(parsed=None, content='{"x": 9}'))
        p, _ = self._provider_with_parse(completion)
        parsed, _ = p.generate_structured("m", [ContentPart("text", "hi")], _Schema)
        self.assertEqual(parsed.x, 9)

    def test_raises_when_no_output(self):
        completion = _FakeCompletion(_FakeMessage(parsed=None, content=None), finish_reason="length")
        p, _ = self._provider_with_parse(completion)
        with self.assertRaises(ValueError):
            p.generate_structured("m", [ContentPart("text", "hi")], _Schema)

    def test_usage_absent_yields_zero(self):
        completion = _FakeCompletion(_FakeMessage(parsed=_Schema(x=1)), usage=None)
        p, _ = self._provider_with_parse(completion)
        _, tokens = p.generate_structured("m", [ContentPart("text", "hi")], _Schema)
        self.assertEqual(tokens, TokenUsage())

    @unittest.skipUnless(
        importlib.util.find_spec("openai") is not None,
        "openai SDK not installed; LengthFinishReasonError handling can't be exercised",
    )
    def test_length_finish_reason_becomes_clear_error(self):
        from openai import LengthFinishReasonError
        p = _bare_provider()
        def raise_length(**kwargs):
            raise LengthFinishReasonError(completion=_FakeCompletion(_FakeMessage(content="{")))
        p.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=raise_length))
        )
        with self.assertRaises(ValueError) as cm:
            p.generate_structured("m", [ContentPart("text", "hi")], _Schema, thinking_budget=4096)
        self.assertIn("output token cap", str(cm.exception))


class GenerateTextTests(unittest.TestCase):
    def test_returns_text_and_usage_no_healing(self):
        usage = _FakeUsage({"prompt_tokens": 30, "completion_tokens": 12,
                            "completion_tokens_details": {"reasoning_tokens": 3}})
        message = _FakeMessage(content="## analysis\nfree text")
        completion = _FakeCompletion(message, usage=usage)
        p = _bare_provider()
        create = mock.Mock(return_value=completion)
        p.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
        )

        text, tokens = p.generate_text("openai/gpt-5", [ContentPart("text", "hi")], thinking_budget=2048)

        self.assertEqual(text, "## analysis\nfree text")
        self.assertEqual(tokens.input_tokens, 30)
        self.assertEqual(tokens.thinking_tokens, 3)
        _, kwargs = create.call_args
        # Free-form text must NOT request the structured-JSON healing plugin.
        plugins = kwargs["extra_body"].get("plugins", [])
        self.assertNotIn("response-healing", [pl["id"] for pl in plugins])
        self.assertNotIn("response_format", kwargs)

    def test_none_content_yields_empty_string(self):
        completion = _FakeCompletion(_FakeMessage(content=None))
        p = _bare_provider()
        p.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=mock.Mock(return_value=completion))
            )
        )
        text, _ = p.generate_text("m", [ContentPart("text", "hi")])
        self.assertEqual(text, "")


class FactoryTests(unittest.TestCase):
    def test_create_provider_requires_key(self):
        with self.assertRaises(ValueError):
            create_provider("")

    def test_create_provider_builds_client_with_openrouter_base_url(self):
        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = FakeOpenAI
        with mock.patch.dict(sys.modules, {"openai": fake_openai}):
            provider = create_provider("sk-or-test", max_retries=4)

        self.assertEqual(provider.name, "openrouter")
        self.assertEqual(captured["base_url"], llm_provider.OPENROUTER_BASE_URL)
        self.assertEqual(captured["api_key"], "sk-or-test")
        self.assertEqual(captured["max_retries"], 4)
        self.assertIn("HTTP-Referer", captured["default_headers"])
        self.assertIn("X-Title", captured["default_headers"])


if __name__ == "__main__":
    unittest.main()
