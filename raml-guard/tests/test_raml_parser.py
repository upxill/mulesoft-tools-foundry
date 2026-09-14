"""Tests for raml_guard.raml_parser: resource/method/param/body/security
extraction, plus !include resolution and its opaque fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from raml_guard.raml_parser import RamlParseError, looks_like_raml, parse_raml_file, parse_raml_text

BASIC_RAML = """#%RAML 1.0
title: Widgets API
version: v1
baseUri: https://api.example.com/widgets

securitySchemes:
  apiKey:
    type: x-custom
    describedBy:
      headers:
        X-CLIENT-ID:
          type: string
        X-CLIENT-SECRET:
          type: string

securedBy: [apiKey]

types:
  Widget:
    type: object
    properties:
      id: string
      color:
        type: string
        enum: [RED, GREEN, BLUE]
      weight: number
      note?: string

/widgets:
  get:
    displayName: List Widgets
    queryParameters:
      color?:
        type: string
        enum: [RED, GREEN, BLUE]
      limit:
        type: integer
        required: true
    responses:
      200:
        body:
          application/json:
            type: array
            items: Widget
  post:
    body:
      application/json:
        type: Widget
    responses:
      201:
        body:
          application/json:
            type: Widget
/widgets/{widgetId}:
  get:
    responses:
      200:
        body:
          application/json:
            type: Widget
      404:
        description: Not found.
"""


def test_looks_like_raml():
    assert looks_like_raml(BASIC_RAML) is True
    assert looks_like_raml("openapi: 3.0.0\ninfo: {}\n") is False
    assert looks_like_raml("") is False


def test_not_raml_raises():
    with pytest.raises(RamlParseError):
        parse_raml_text("just: yaml\n", "not-raml.yaml")


def test_malformed_yaml_raises():
    with pytest.raises(RamlParseError):
        parse_raml_text("#%RAML 1.0\nfoo: [unterminated\n", "bad.raml")


def test_resources_and_methods_extracted():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    assert "/widgets" in spec.resources
    assert "/widgets/{widgetId}" in spec.resources
    widgets = spec.resources["/widgets"]
    assert set(widgets.methods) == {"get", "post"}
    detail = spec.resources["/widgets/{widgetId}"]
    assert set(detail.methods) == {"get"}


def test_path_param_auto_declared_as_required_string():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    detail = spec.resources["/widgets/{widgetId}"]
    assert "widgetId" in detail.uri_params
    p = detail.uri_params["widgetId"]
    assert p.required is True
    assert p.type == "string"


def test_query_parameters_required_flag_and_type():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    get_widgets = spec.resources["/widgets"].methods["get"]
    color = get_widgets.query_params["color"]
    limit = get_widgets.query_params["limit"]
    assert color.required is False
    assert color.type == "string"
    assert color.enum == ["RED", "GREEN", "BLUE"]
    assert limit.required is True
    assert limit.type == "integer"


def test_request_and_response_bodies():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    post_widgets = spec.resources["/widgets"].methods["post"]
    assert "application/json" in post_widgets.bodies
    body_schema = post_widgets.bodies["application/json"].schema
    assert body_schema["type"] == "object"
    assert "color" in body_schema["properties"]
    assert body_schema["properties"]["note"]["required"] is False
    assert body_schema["properties"]["id"]["required"] is True

    get_widgets = spec.resources["/widgets"].methods["get"]
    resp_200 = get_widgets.responses["200"]
    assert "application/json" in resp_200.bodies
    array_schema = resp_200.bodies["application/json"].schema
    assert array_schema["type"] == "array"
    assert array_schema["items"]["type"] == "object"
    assert array_schema["items"]["properties"]["weight"]["type"] == "number"


def test_response_status_codes():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    detail_get = spec.resources["/widgets/{widgetId}"].methods["get"]
    assert set(detail_get.responses) == {"200", "404"}
    assert detail_get.responses["404"].description == "Not found."


def test_top_level_security():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    assert spec.secured_by == ["apiKey"]
    assert "apiKey" in spec.security_schemes


def test_enum_widening_source_values_preserved():
    spec = parse_raml_text(BASIC_RAML, "basic.raml")
    widget_type = spec.resources["/widgets"].methods["post"].bodies["application/json"].schema
    assert widget_type["properties"]["color"]["enum"] == ["RED", "GREEN", "BLUE"]


# ---------------------------------------------------------------------------
# !include resolution
# ---------------------------------------------------------------------------


def test_include_resolved_relative_to_including_file(tmp_path: Path):
    (tmp_path / "types").mkdir()
    (tmp_path / "types" / "order.raml").write_text(
        """#%RAML 1.0 DataType
type: object
properties:
  id: string
  total: number
""",
        encoding="utf-8",
    )
    main_raml = tmp_path / "main.raml"
    main_raml.write_text(
        """#%RAML 1.0
title: Orders
types:
  Order: !include types/order.raml
/orders:
  post:
    body:
      application/json:
        type: Order
    responses:
      201:
        body:
          application/json:
            type: Order
""",
        encoding="utf-8",
    )
    spec = parse_raml_file(str(main_raml))
    schema = spec.resources["/orders"].methods["post"].bodies["application/json"].schema
    assert schema["type"] == "object"
    assert schema["properties"]["id"]["type"] == "string"
    assert schema["properties"]["total"]["type"] == "number"
    assert spec.unresolved_includes == []


def test_include_resolved_json_schema_fragment(tmp_path: Path):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "order.json").write_text(
        """{"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}""",
        encoding="utf-8",
    )
    main_raml = tmp_path / "main.raml"
    main_raml.write_text(
        """#%RAML 1.0
title: Orders
/orders:
  post:
    body:
      application/json:
        type: !include schemas/order.json
""",
        encoding="utf-8",
    )
    spec = parse_raml_file(str(main_raml))
    schema = spec.resources["/orders"].methods["post"].bodies["application/json"].schema
    assert schema["type"] == "object"
    assert schema["properties"]["id"]["required"] is True


def test_missing_include_falls_back_to_opaque_without_crashing(tmp_path: Path):
    main_raml = tmp_path / "main.raml"
    main_raml.write_text(
        """#%RAML 1.0
title: Orders
types:
  Order: !include types/does_not_exist.raml
/orders:
  post:
    body:
      application/json:
        type: Order
""",
        encoding="utf-8",
    )
    spec = parse_raml_file(str(main_raml))
    schema = spec.resources["/orders"].methods["post"].bodies["application/json"].schema
    assert schema["type"] == "opaque"
    assert schema["ref"] == "types/does_not_exist.raml"
    assert "types/does_not_exist.raml" in spec.unresolved_includes


def test_nested_include_resolves_relative_to_its_own_directory(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    # a/mid.raml includes b/leaf.raml -- relative to a/, not to the project root.
    (tmp_path / "a" / "b" / "leaf.raml").write_text(
        """#%RAML 1.0 DataType
type: object
properties:
  leafField: string
""",
        encoding="utf-8",
    )
    (tmp_path / "a" / "mid.raml").write_text(
        """#%RAML 1.0 DataType
type: object
properties:
  midField: !include b/leaf.raml
""",
        encoding="utf-8",
    )
    main_raml = tmp_path / "main.raml"
    main_raml.write_text(
        """#%RAML 1.0
title: Orders
types:
  Wrapper: !include a/mid.raml
/orders:
  post:
    body:
      application/json:
        type: Wrapper
""",
        encoding="utf-8",
    )
    spec = parse_raml_file(str(main_raml))
    schema = spec.resources["/orders"].methods["post"].bodies["application/json"].schema
    assert schema["type"] == "object"
    mid_field = schema["properties"]["midField"]
    assert mid_field["type"] == "object"
    assert mid_field["properties"]["leafField"]["type"] == "string"
    assert spec.unresolved_includes == []


def test_resource_and_method_level_secured_by(tmp_path: Path):
    raml = """#%RAML 1.0
title: Orders
securitySchemes:
  scheme1:
    type: x-custom
securedBy: [scheme1]
/open:
  get:
    securedBy: [null]
    responses:
      200:
        description: ok
/inherits:
  get:
    responses:
      200:
        description: ok
/resource-override:
  securedBy: []
  get:
    responses:
      200:
        description: ok
"""
    spec = parse_raml_text(raml, "sec.raml")
    assert spec.resources["/open"].methods["get"].secured_by == []
    assert spec.resources["/inherits"].methods["get"].secured_by is None
    assert spec.resources["/resource-override"].secured_by == []
