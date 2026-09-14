import json
import sys
import types

import pytest

from munit_shadow.enrich import enrich_new_call_payloads
from munit_shadow.xml_parser import FlowInfo, OutboundCall


def _make_flow():
    flow = FlowInfo(name="orders-process-flow", kind="flow")
    flow.outbound_calls = [
        OutboundCall(flow_name=flow.name, operation="db:select", config_ref="ordersDbConfig", doc_name=None)
    ]
    return flow


def test_no_anthropic_package_returns_empty_dict_without_crashing(monkeypatch):
    # Simulate the package genuinely not being installed.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    flow = _make_flow()
    result = enrich_new_call_payloads(flow, "<flow/>", flow.outbound_calls, model="claude-opus-5")
    assert result == {}


def _install_fake_anthropic(monkeypatch, response_text, capture):
    fake_module = types.ModuleType("anthropic")

    class _TextBlock:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _Response:
        def __init__(self, text):
            self.content = [_TextBlock(text)]

    class _Messages:
        def create(self, **kwargs):
            capture.update(kwargs)
            return _Response(response_text)

    class _Anthropic:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    fake_module.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)


def test_successful_response_is_parsed_and_matched_by_operation_and_config_ref(monkeypatch):
    capture = {}
    payload = {
        "mocks": [
            {
                "operation": "db:select",
                "config_ref": "ordersDbConfig",
                "payload_expression": "#[[{ id: 1, status: 'CONFIRMED' }]]",
            }
        ]
    }
    _install_fake_anthropic(monkeypatch, json.dumps(payload), capture)

    flow = _make_flow()
    result = enrich_new_call_payloads(flow, "<flow/>", flow.outbound_calls, model="claude-opus-5")

    assert result[("db:select", "ordersDbConfig")] == "#[[{ id: 1, status: 'CONFIRMED' }]]"
    assert capture["model"] == "claude-opus-5"
    assert capture["thinking"] == {"type": "adaptive"}
    assert "db:select" in capture["messages"][0]["content"]


def test_malformed_json_response_falls_back_to_empty_dict(monkeypatch):
    capture = {}
    _install_fake_anthropic(monkeypatch, "not json at all, sorry", capture)

    flow = _make_flow()
    result = enrich_new_call_payloads(flow, "<flow/>", flow.outbound_calls, model="claude-opus-5")
    assert result == {}


def test_api_exception_falls_back_to_empty_dict_without_crashing(monkeypatch):
    fake_module = types.ModuleType("anthropic")

    class _Anthropic:
        def __init__(self, *a, **kw):
            raise RuntimeError("Could not resolve authentication method")

    fake_module.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    flow = _make_flow()
    result = enrich_new_call_payloads(flow, "<flow/>", flow.outbound_calls, model="claude-opus-5")
    assert result == {}


def test_no_new_calls_short_circuits_without_importing_anthropic():
    flow = _make_flow()
    result = enrich_new_call_payloads(flow, "<flow/>", [], model="claude-opus-5")
    assert result == {}
