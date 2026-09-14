"""Offline tests: parse examples/orders_api.raml and assert correct operation,
parameter, and security-scheme extraction. No network, no external services."""

from pathlib import Path

from mule_to_mcp import raml_parser

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
ORDERS_RAML = EXAMPLES_DIR / "orders_api.raml"


def _load():
    raw_text = ORDERS_RAML.read_text(encoding="utf-8")
    return raml_parser.load_raml(raw_text)


def test_looks_like_raml_detects_header():
    raw = ORDERS_RAML.read_text(encoding="utf-8")
    assert raml_parser.looks_like_raml(raw) is True
    assert raml_parser.looks_like_raml("openapi: 3.0.0\ninfo:\n  title: x\n") is False


def test_load_raml_rejects_non_raml_text():
    import pytest

    with pytest.raises(raml_parser.RamlParseError):
        raml_parser.load_raml("title: not raml\n")


def test_get_api_title_and_base_url():
    doc = _load()
    assert raml_parser.get_api_title(doc) == "Orders API"
    assert raml_parser.get_base_url(doc) == "http://127.0.0.1:8091"


def test_extract_operations_finds_all_four():
    doc = _load()
    ops = raml_parser.extract_operations(doc)
    seen = {(op.method, op.path) for op in ops}
    assert seen == {
        ("get", "/orders"),
        ("post", "/orders"),
        ("get", "/orders/{orderId}"),
        ("put", "/orders/{orderId}"),
    }


def test_list_orders_has_optional_status_query_param():
    doc = _load()
    ops = raml_parser.extract_operations(doc)
    list_orders = next(op for op in ops if op.method == "get" and op.path == "/orders")
    assert list_orders.summary == "List Orders"
    status_params = [p for p in list_orders.parameters if p.name == "status"]
    assert len(status_params) == 1
    assert status_params[0].location == "query"
    assert status_params[0].required is False


def test_get_order_by_id_has_required_path_param():
    doc = _load()
    ops = raml_parser.extract_operations(doc)
    get_order = next(op for op in ops if op.method == "get" and op.path == "/orders/{orderId}")
    order_id_params = [p for p in get_order.parameters if p.name == "orderId"]
    assert len(order_id_params) == 1
    assert order_id_params[0].location == "path"
    assert order_id_params[0].required is True


def test_create_order_has_request_body_with_resolved_type():
    doc = _load()
    ops = raml_parser.extract_operations(doc)
    create_order = next(op for op in ops if op.method == "post" and op.path == "/orders")
    assert create_order.request_body is not None
    schema = create_order.request_body.schema
    assert schema["type"] == "object"
    assert "customerName" in schema["properties"]
    assert "items" in schema["properties"]
    assert schema["properties"]["items"]["type"] == "array"


def test_update_order_status_body_resolves_named_type():
    doc = _load()
    ops = raml_parser.extract_operations(doc)
    update = next(op for op in ops if op.method == "put" and op.path == "/orders/{orderId}")
    assert update.request_body is not None
    assert "status" in update.request_body.schema["properties"]


def test_detect_security_finds_client_id_enforcement():
    doc = _load()
    detected = raml_parser.detect_security(doc)
    assert detected.kind == "client-id"
    assert detected.client_id_header == "client_id"
    assert detected.client_secret_header == "client_secret"


def test_detect_security_none_when_no_schemes():
    doc = {"title": "No Auth API", "baseUri": "http://x", "/things": {"get": {}}}
    detected = raml_parser.detect_security(doc)
    assert detected.kind == "none"
