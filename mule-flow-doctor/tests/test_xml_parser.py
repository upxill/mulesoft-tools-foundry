"""Offline, real assertions against the bundled example apps.

No network access, no LLM calls -- these test the deterministic XML parser
directly against examples/seeded_issues_app and examples/clean_app.
"""

from pathlib import Path

import pytest

from mule_flow_doctor import xml_parser

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
SEEDED_APP = EXAMPLES_DIR / "seeded_issues_app"
CLEAN_APP = EXAMPLES_DIR / "clean_app"


@pytest.fixture(scope="module")
def seeded():
    return xml_parser.parse_app(SEEDED_APP)


@pytest.fixture(scope="module")
def clean():
    return xml_parser.parse_app(CLEAN_APP)


# --------------------------------------------------------------------------
# seeded_issues_app
# --------------------------------------------------------------------------


def test_seeded_flows_discovered(seeded):
    assert set(seeded.flows.keys()) == {
        "orders-process-flow",
        "billing-calculate-subflow",
    }
    assert seeded.flows["orders-process-flow"].kind == "flow"
    assert seeded.flows["billing-calculate-subflow"].kind == "sub-flow"


def test_seeded_flow_ref_edge(seeded):
    assert ("orders-process-flow", "billing-calculate-subflow") in seeded.edges


def test_seeded_entry_point_detection(seeded):
    orders = seeded.flows["orders-process-flow"]
    assert orders.is_entry_point is True
    assert orders.entry_point_type == "http:listener"

    billing = seeded.flows["billing-calculate-subflow"]
    assert billing.is_entry_point is False


def test_seeded_missing_error_handler(seeded):
    # Seeded issue: orders-process-flow calls two external services but has
    # no error-handler anywhere in its subtree.
    assert seeded.flows["orders-process-flow"].has_error_handler is False


def test_seeded_connector_calls_and_missing_reconnection(seeded):
    orders = seeded.flows["orders-process-flow"]
    ops = {c.operation for c in orders.connector_calls}
    assert "http:request" in ops
    assert len(orders.connector_calls) == 2

    config_refs = {c.config_ref for c in orders.connector_calls}
    assert config_refs == {"paymentServiceConfig", "inventoryServiceConfig"}

    # Seeded issue: neither outbound config has a reconnection strategy.
    for call in orders.connector_calls:
        assert call.has_reconnection is False


def test_seeded_secret_candidates(seeded):
    # Seeded issues: db password in global-config.xml, API key in
    # orders-flow.xml's http:headers CDATA body.
    assert len(seeded.secrets) >= 2

    files_with_secrets = {s.file for s in seeded.secrets}
    assert any("global-config.xml" in f for f in files_with_secrets)
    assert any("orders-flow.xml" in f for f in files_with_secrets)

    db_secret = next(s for s in seeded.secrets if "global-config.xml" in s.file)
    assert db_secret.flow_name is None  # lives in a global config, not a flow
    assert "password" in db_secret.location

    api_key_secret = next(s for s in seeded.secrets if "orders-flow.xml" in s.file)
    assert api_key_secret.flow_name == "orders-process-flow"


def test_seeded_dataweave_inefficiency_detected(seeded):
    assert len(seeded.dataweave_issues) >= 1
    issue = next(
        d for d in seeded.dataweave_issues if d.flow_name == "billing-calculate-subflow"
    )
    assert issue.collection == "payload.orders"
    assert issue.pass_count >= 2


def test_seeded_summarize_is_json_serializable(seeded):
    import json

    summary = xml_parser.summarize(seeded)
    json.dumps(summary)  # must not raise
    assert len(summary["flows"]) == 2
    assert len(summary["secrets"]) >= 2
    assert len(summary["dataweave_issues"]) >= 1


# --------------------------------------------------------------------------
# clean_app -- must NOT be falsely flagged
# --------------------------------------------------------------------------


def test_clean_app_flow_discovered(clean):
    assert "catalog-lookup-flow" in clean.flows
    assert "format-items-subflow" in clean.flows


def test_clean_app_has_error_handler(clean):
    assert clean.flows["catalog-lookup-flow"].has_error_handler is True


def test_clean_app_has_reconnection(clean):
    flow = clean.flows["catalog-lookup-flow"]
    assert len(flow.connector_calls) == 1
    assert flow.connector_calls[0].has_reconnection is True


def test_clean_app_no_secrets(clean):
    assert clean.secrets == []


def test_clean_app_no_dataweave_inefficiency(clean):
    assert clean.dataweave_issues == []


def test_clean_app_flow_ref_edge(clean):
    assert ("catalog-lookup-flow", "format-items-subflow") in clean.edges
