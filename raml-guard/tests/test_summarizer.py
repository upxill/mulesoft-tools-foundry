"""Tests for the optional summarizer module.

This environment has no `anthropic` package installed and no
`ANTHROPIC_API_KEY` (see README "LLM enhancement" section for full
disclosure) so these tests cover exactly two things, honestly:

1. The real, currently-installed behavior: `summarize_changes` raises a
   clean `SummarizerError` (not a crash) when `anthropic` isn't installed --
   this is the actual state of this environment, verified for real.
2. With a stubbed-in fake `anthropic` module (no real network/API call),
   the rest of the pipeline -- prompt building from real `Change` objects,
   model/env resolution, and response-text extraction -- runs end to end
   through the exact same `summarizer._call_claude` code path the CLI uses.
   The "model response" is scripted by the test, not a real Claude call.
"""

from __future__ import annotations

import sys
import types

import pytest

from raml_guard.diff_engine import BREAKING, NON_BREAKING, Change
from raml_guard.summarizer import DEFAULT_MODEL, SummarizerError, summarize_changes


SAMPLE_CHANGES = [
    Change(BREAKING, "GET /orders/{id}", "method-removed", "Resource '/orders/{id}' was removed entirely."),
    Change(NON_BREAKING, "GET /orders query param 'sortBy'", "param-added", "New optional parameter 'sortBy' added."),
]


def test_summarize_changes_raises_cleanly_without_anthropic_installed(monkeypatch):
    for mod_name in list(sys.modules):
        if mod_name == "anthropic" or mod_name.startswith("anthropic."):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(SummarizerError):
        summarize_changes(SAMPLE_CHANGES)


def test_summarize_changes_with_stubbed_anthropic_client(monkeypatch):
    captured_kwargs = {}

    class _FakeTextBlock:
        type = "text"
        text = "Release note: one breaking change (removed GET /orders/{id}) and one non-breaking addition (sortBy)."

    class _FakeResponse:
        content = [_FakeTextBlock()]

    class _FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeAnthropic:
        def __init__(self):
            self.messages = _FakeMessages()

    fake_module = types.SimpleNamespace(Anthropic=_FakeAnthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    result = summarize_changes(SAMPLE_CHANGES, model="claude-opus-5-test")

    assert "breaking change" in result.lower()
    assert captured_kwargs["model"] == "claude-opus-5-test"
    assert captured_kwargs["thinking"] == {"type": "adaptive"}
    assert "GET /orders/{id}" in captured_kwargs["messages"][0]["content"]


def test_default_model_used_when_no_override(monkeypatch):
    captured_kwargs = {}

    class _FakeTextBlock:
        type = "text"
        text = "ok"

    class _FakeResponse:
        content = [_FakeTextBlock()]

    class _FakeMessages:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeResponse()

    class _FakeAnthropic:
        def __init__(self):
            self.messages = _FakeMessages()

    fake_module = types.SimpleNamespace(Anthropic=_FakeAnthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    summarize_changes(SAMPLE_CHANGES)
    assert captured_kwargs["model"] == DEFAULT_MODEL
