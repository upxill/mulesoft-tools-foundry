"""Offline tests for enrich.py.

This environment has no ANTHROPIC_API_KEY, no ANTHROPIC_AUTH_TOKEN, and no
`ant` CLI/OAuth profile (verified: `ant` is not on PATH and both env vars are
unset), so a real Claude call cannot be exercised end-to-end here -- see
README.md's "Optional --enrich: live vs. verified-via-mock" section for the
full disclosure, including the real (unstubbed) CLI run that reached this
exact authentication error.

What CAN be verified without credentials, and what these tests actually
check: the exact request shape enrich.py sends (model, thinking, message
content) via a fake `anthropic` module substituted into sys.modules, and
that the response-parsing / XML-mutation logic correctly applies a
model-shaped JSON response to the right mock-when/assert-that elements.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from mule_munit_scaffold import enrich
from mule_munit_scaffold.scaffold import build_test_suite
from mule_munit_scaffold.xml_parser import parse_app

CORE_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(CORE_HEADER + body + "\n</mule>\n", encoding="utf-8")
    return p


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, capture, response_text):
        self._capture = capture
        self._response_text = response_text

    def create(self, **kwargs):
        self._capture.append(kwargs)
        return _FakeResponse(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, capture, response_text):
        self.messages = _FakeMessages(capture, response_text)


def _install_fake_anthropic(monkeypatch, capture, response_text):
    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = lambda: _FakeAnthropicClient(capture, response_text)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)


def test_enrich_sends_expected_request_shape(tmp_path, monkeypatch):
    xml = _write(
        tmp_path,
        "flow.xml",
        """
        <flow name="orders-flow">
            <http:request config-ref="cfg" path="/x" doc:name="Call it"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    flows = list(parsed.flows.values())

    capture = []
    response_json = (
        '{"mocks": [{"operation": "http:request", "config_ref": "cfg", '
        '"payload_expression": "#[{status: 200}]", "media_type": "application/json"}], '
        '"assertion": {"expression": "#[payload.status]", "is": "#[MunitTools::equalTo(200)]"}}'
    )
    _install_fake_anthropic(monkeypatch, capture, response_json)

    flow_xml_by_name = {f.name: "<flow name='orders-flow'/>" for f in flows}

    notes = enrich.enrich_test_suite(tree, flows, flow_xml_by_name, model="claude-opus-5")

    assert len(capture) == 1
    call_kwargs = capture[0]
    assert call_kwargs["model"] == "claude-opus-5"
    assert call_kwargs["thinking"] == {"type": "adaptive"}
    assert call_kwargs["messages"][0]["role"] == "user"
    assert "orders-flow" in call_kwargs["messages"][0]["content"]

    assert any("enriched 1 mock payload" in n for n in notes)


def test_enrich_applies_payload_and_assertion(tmp_path, monkeypatch):
    xml = _write(
        tmp_path,
        "flow2.xml",
        """
        <flow name="billing-flow">
            <http:request config-ref="paymentConfig" path="/charge" doc:name="Charge"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    flows = list(parsed.flows.values())

    response_json = (
        '{"mocks": [{"operation": "http:request", "config_ref": "paymentConfig", '
        '"payload_expression": "#[{approved: true, transactionId: \\"TX-1\\"}]", '
        '"media_type": "application/json"}], '
        '"assertion": {"expression": "#[payload.approved]", "is": "#[MunitTools::equalTo(true)]"}}'
    )
    capture = []
    _install_fake_anthropic(monkeypatch, capture, response_json)

    flow_xml_by_name = {f.name: "<flow name='billing-flow'/>" for f in flows}
    enrich.enrich_test_suite(tree, flows, flow_xml_by_name, model="claude-opus-5")

    root = tree.getroot()
    payload_elem = root.find(
        ".//{http://www.mulesoft.org/schema/mule/munit-tools}payload"
    )
    assert payload_elem.get("value") == '#[{approved: true, transactionId: "TX-1"}]'

    assert_elem = root.find(
        ".//{http://www.mulesoft.org/schema/mule/munit-tools}assert-that"
    )
    assert assert_elem.get("expression") == "#[payload.approved]"
    assert assert_elem.get("is") == "#[MunitTools::equalTo(true)]"


def test_enrich_handles_bad_json_gracefully(tmp_path, monkeypatch):
    xml = _write(
        tmp_path,
        "flow3.xml",
        """
        <flow name="broken-enrich-flow">
            <http:request config-ref="c" path="/x" doc:name="Call"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    flows = list(parsed.flows.values())

    capture = []
    _install_fake_anthropic(monkeypatch, capture, "not json at all, sorry")

    flow_xml_by_name = {f.name: "<flow/>" for f in flows}
    notes = enrich.enrich_test_suite(tree, flows, flow_xml_by_name, model="claude-opus-5")

    assert any("enrichment failed" in n for n in notes)
    # Original deterministic placeholder must still be there, untouched.
    root = tree.getroot()
    payload_elem = root.find(
        ".//{http://www.mulesoft.org/schema/mule/munit-tools}payload"
    )
    assert "mocked" in payload_elem.get("value")


def test_enrich_without_anthropic_installed_raises_clear_error(tmp_path, monkeypatch):
    xml = _write(
        tmp_path,
        "flow4.xml",
        """
        <flow name="no-anthropic-flow">
            <http:request config-ref="c" path="/x" doc:name="Call"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    flows = list(parsed.flows.values())

    monkeypatch.setitem(sys.modules, "anthropic", None)  # simulate ImportError
    with pytest.raises(RuntimeError, match="anthropic"):
        enrich.enrich_test_suite(tree, flows, {f.name: "" for f in flows}, model="claude-opus-5")
