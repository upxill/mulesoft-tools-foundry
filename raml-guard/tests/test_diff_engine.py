"""Tests for raml_guard.diff_engine: every classification rule, both
"should flag as breaking" and "should NOT flag as breaking" cases."""

from __future__ import annotations

from pathlib import Path

from raml_guard.diff_engine import BREAKING, INFORMATIONAL, NON_BREAKING, diff_specs
from raml_guard.raml_parser import parse_raml_file, parse_raml_text

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _diff(old_text: str, new_text: str):
    old_spec = parse_raml_text(old_text, "old.raml")
    new_spec = parse_raml_text(new_text, "new.raml")
    return diff_specs(old_spec, new_spec)


def _kinds(changes, category=None):
    if category is None:
        return {c.kind for c in changes}
    return {c.kind for c in changes if c.category == category}


def _by_kind(changes, kind):
    return [c for c in changes if c.kind == kind]


# ---------------------------------------------------------------------------
# Resource / method presence
# ---------------------------------------------------------------------------


def test_method_removed_is_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
  post:
    responses:
      201:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    removed = _by_kind(changes, "method-removed")
    assert len(removed) == 1
    assert removed[0].category == BREAKING
    assert "POST /orders" in removed[0].location


def test_method_added_is_non_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
  post:
    responses:
      201:
        description: ok
"""
    changes = _diff(old, new)
    added = _by_kind(changes, "method-added")
    assert len(added) == 1
    assert added[0].category == NON_BREAKING


def test_resource_removed_is_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
/orders/{id}:
  get:
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    assert any(c.kind == "method-removed" and c.category == BREAKING and "/orders/{id}" in c.location for c in changes)


def test_resource_added_is_non_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
/orders/{id}:
  get:
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    assert any(c.kind == "method-added" and c.category == NON_BREAKING and "/orders/{id}" in c.location for c in changes)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def _method_with_query_params(old_params: str, new_params: str):
    old = f"""#%RAML 1.0
title: T
/orders:
  get:
    queryParameters:
{old_params}
    responses:
      200:
        description: ok
"""
    new = f"""#%RAML 1.0
title: T
/orders:
  get:
    queryParameters:
{new_params}
    responses:
      200:
        description: ok
"""
    return _diff(old, new)


def test_param_optional_to_required_is_breaking():
    changes = _method_with_query_params(
        "      status?:\n        type: string\n",
        "      status:\n        type: string\n",
    )
    hits = _by_kind(changes, "param-now-required")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_param_required_to_optional_is_non_breaking():
    changes = _method_with_query_params(
        "      status:\n        type: string\n",
        "      status?:\n        type: string\n",
    )
    hits = _by_kind(changes, "param-now-optional")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_new_optional_param_is_not_breaking():
    """Tricky case explicitly called out in the spec: adding an OPTIONAL
    param must never be flagged breaking."""
    changes = _method_with_query_params(
        "      status?:\n        type: string\n",
        "      status?:\n        type: string\n      sortBy?:\n        type: string\n",
    )
    hits = _by_kind(changes, "param-added")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING
    assert not any(c.kind.startswith("param-added") and c.category == BREAKING for c in changes)


def test_new_required_param_is_breaking():
    """Tricky case explicitly called out in the spec: adding a REQUIRED
    param must be flagged breaking."""
    changes = _method_with_query_params(
        "      status?:\n        type: string\n",
        "      status?:\n        type: string\n      customerId:\n        type: string\n",
    )
    hits = _by_kind(changes, "param-added-required")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_param_removed_is_informational():
    changes = _method_with_query_params(
        "      status?:\n        type: string\n      limit?:\n        type: integer\n",
        "      status?:\n        type: string\n",
    )
    hits = _by_kind(changes, "param-removed")
    assert len(hits) == 1
    assert hits[0].category == INFORMATIONAL


def test_param_type_narrowed_is_breaking():
    changes = _method_with_query_params(
        "      limit:\n        type: string\n",
        "      limit:\n        type: integer\n",
    )
    hits = _by_kind(changes, "type-narrowed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_param_type_widened_is_non_breaking():
    changes = _method_with_query_params(
        "      limit:\n        type: integer\n",
        "      limit:\n        type: number\n",
    )
    hits = _by_kind(changes, "type-widened")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_param_enum_value_removed_is_breaking():
    changes = _method_with_query_params(
        "      status:\n        type: string\n        enum: [A, B, C]\n",
        "      status:\n        type: string\n        enum: [A, B]\n",
    )
    hits = _by_kind(changes, "enum-value-removed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_param_enum_value_added_is_non_breaking():
    changes = _method_with_query_params(
        "      status:\n        type: string\n        enum: [A, B]\n",
        "      status:\n        type: string\n        enum: [A, B, C]\n",
    )
    hits = _by_kind(changes, "enum-value-added")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_param_default_changed_is_informational():
    changes = _method_with_query_params(
        "      limit:\n        type: integer\n        default: 10\n",
        "      limit:\n        type: integer\n        default: 20\n",
    )
    hits = _by_kind(changes, "default-changed")
    assert len(hits) == 1
    assert hits[0].category == INFORMATIONAL


# ---------------------------------------------------------------------------
# Response status codes
# ---------------------------------------------------------------------------


def test_response_status_removed_is_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
      404:
        description: not found
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "response-status-removed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_response_status_added_is_non_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
      404:
        description: not found
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "response-status-added")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


# ---------------------------------------------------------------------------
# Body / response schema field diffs
# ---------------------------------------------------------------------------


def _post_with_bodies(old_props: str, new_props: str):
    old = f"""#%RAML 1.0
title: T
/orders:
  post:
    body:
      application/json:
        type: object
        properties:
{old_props}
    responses:
      201:
        description: ok
"""
    new = f"""#%RAML 1.0
title: T
/orders:
  post:
    body:
      application/json:
        type: object
        properties:
{new_props}
    responses:
      201:
        description: ok
"""
    return _diff(old, new)


def test_request_body_field_removed_is_breaking():
    changes = _post_with_bodies(
        "          customerName: string\n          notes?: string\n",
        "          customerName: string\n",
    )
    hits = _by_kind(changes, "field-removed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_request_body_new_required_field_is_breaking():
    changes = _post_with_bodies(
        "          customerName: string\n",
        "          customerName: string\n          taxId: string\n",
    )
    hits = _by_kind(changes, "field-added-required")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_request_body_new_optional_field_is_non_breaking():
    changes = _post_with_bodies(
        "          customerName: string\n",
        "          customerName: string\n          giftMessage?: string\n",
    )
    hits = _by_kind(changes, "field-added")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_request_body_field_type_narrowed_is_breaking():
    changes = _post_with_bodies(
        "          total: string\n",
        "          total: integer\n",
    )
    hits = _by_kind(changes, "type-narrowed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def _get_with_response_bodies(old_props: str, new_props: str):
    old = f"""#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        body:
          application/json:
            type: object
            properties:
{old_props}
"""
    new = f"""#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        body:
          application/json:
            type: object
            properties:
{new_props}
"""
    return _diff(old, new)


def test_response_body_new_field_is_non_breaking_even_if_required():
    """Per the spec: a NEW field added to a response body is non-breaking
    regardless of whether it's marked required in the new schema."""
    changes = _get_with_response_bodies(
        "              id: string\n",
        "              id: string\n              total: number\n",
    )
    hits = _by_kind(changes, "field-added")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_response_body_field_removed_is_breaking():
    changes = _get_with_response_bodies(
        "              id: string\n              total: number\n",
        "              id: string\n",
    )
    hits = _by_kind(changes, "field-removed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_response_body_field_type_narrowed_is_breaking():
    changes = _get_with_response_bodies(
        "              total: number\n",
        "              total: integer\n",
    )
    hits = _by_kind(changes, "type-narrowed")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_response_body_field_type_widened_is_non_breaking():
    changes = _get_with_response_bodies(
        "              total: integer\n",
        "              total: number\n",
    )
    hits = _by_kind(changes, "type-widened")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_response_body_field_required_to_optional_is_informational():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        body:
          application/json:
            type: object
            properties:
              total:
                type: number
                required: true
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        body:
          application/json:
            type: object
            properties:
              total?: number
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "field-now-optional")
    assert len(hits) == 1
    assert hits[0].category == INFORMATIONAL


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_security_added_where_none_existed_is_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
securitySchemes:
  apiKey:
    type: x-custom
/orders:
  get:
    securedBy: [apiKey]
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "security-added")
    assert len(hits) == 1
    assert hits[0].category == BREAKING


def test_security_removed_entirely_is_non_breaking():
    old = """#%RAML 1.0
title: T
securitySchemes:
  apiKey:
    type: x-custom
securedBy: [apiKey]
/orders:
  get:
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
securitySchemes:
  apiKey:
    type: x-custom
/orders:
  get:
    securedBy: []
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "security-removed")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


# ---------------------------------------------------------------------------
# Documentation / cosmetic
# ---------------------------------------------------------------------------


def test_description_change_is_non_breaking():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    description: Old description.
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    description: New, better description.
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "description-changed")
    assert len(hits) == 1
    assert hits[0].category == NON_BREAKING


def test_display_name_change_is_informational():
    old = """#%RAML 1.0
title: T
/orders:
  get:
    displayName: List Orders
    responses:
      200:
        description: ok
"""
    new = """#%RAML 1.0
title: T
/orders:
  get:
    displayName: Get All Orders
    responses:
      200:
        description: ok
"""
    changes = _diff(old, new)
    hits = _by_kind(changes, "display-name-changed")
    assert len(hits) == 1
    assert hits[0].category == INFORMATIONAL


# ---------------------------------------------------------------------------
# Opaque / unresolved !include handling
# ---------------------------------------------------------------------------


def test_opaque_schema_unchanged_ref_produces_no_diff(tmp_path: Path):
    raml_text = """#%RAML 1.0
title: T
types:
  Order: !include missing.raml
/orders:
  post:
    body:
      application/json:
        type: Order
"""
    old_path = tmp_path / "old.raml"
    new_path = tmp_path / "new.raml"
    old_path.write_text(raml_text, encoding="utf-8")
    new_path.write_text(raml_text, encoding="utf-8")
    old_spec = parse_raml_file(str(old_path))
    new_spec = parse_raml_file(str(new_path))
    changes = diff_specs(old_spec, new_spec)
    # Same unresolved ref on both sides -- must not fabricate a diff about it.
    assert not any(c.location.startswith("POST /orders body") for c in changes)


def test_opaque_schema_changed_ref_is_informational(tmp_path: Path):
    old_path = tmp_path / "old.raml"
    new_path = tmp_path / "new.raml"
    old_path.write_text(
        """#%RAML 1.0
title: T
types:
  Order: !include missing_a.raml
/orders:
  post:
    body:
      application/json:
        type: Order
""",
        encoding="utf-8",
    )
    new_path.write_text(
        """#%RAML 1.0
title: T
types:
  Order: !include missing_b.raml
/orders:
  post:
    body:
      application/json:
        type: Order
""",
        encoding="utf-8",
    )
    old_spec = parse_raml_file(str(old_path))
    new_spec = parse_raml_file(str(new_path))
    changes = diff_specs(old_spec, new_spec)
    hits = _by_kind(changes, "opaque-ref-changed")
    assert len(hits) == 1
    assert hits[0].category == INFORMATIONAL


# ---------------------------------------------------------------------------
# End-to-end against the bundled examples
# ---------------------------------------------------------------------------


def test_bundled_v1_vs_breaking_example_has_breaking_changes():
    old_spec = parse_raml_file(str(EXAMPLES_DIR / "orders_api_v1.raml"))
    new_spec = parse_raml_file(str(EXAMPLES_DIR / "orders_api_v2_breaking.raml"))
    changes = diff_specs(old_spec, new_spec)
    breaking = [c for c in changes if c.category == BREAKING]
    assert len(breaking) >= 2
    assert any(c.kind == "method-removed" for c in breaking)


def test_bundled_v1_vs_safe_example_has_zero_breaking_changes():
    old_spec = parse_raml_file(str(EXAMPLES_DIR / "orders_api_v1.raml"))
    new_spec = parse_raml_file(str(EXAMPLES_DIR / "orders_api_v2_safe.raml"))
    changes = diff_specs(old_spec, new_spec)
    breaking = [c for c in changes if c.category == BREAKING]
    assert breaking == []
    non_breaking = [c for c in changes if c.category == NON_BREAKING]
    assert len(non_breaking) > 0


def test_diff_is_deterministic_across_runs():
    old_spec = parse_raml_file(str(EXAMPLES_DIR / "orders_api_v1.raml"))
    new_spec = parse_raml_file(str(EXAMPLES_DIR / "orders_api_v2_safe.raml"))
    changes_a = diff_specs(old_spec, new_spec)
    changes_b = diff_specs(old_spec, new_spec)
    assert [(c.category, c.location, c.kind) for c in changes_a] == [
        (c.category, c.location, c.kind) for c in changes_b
    ]
