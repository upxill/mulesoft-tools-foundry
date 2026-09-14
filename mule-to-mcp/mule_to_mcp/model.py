"""Shared internal model that both parsers (RAML and OpenAPI) feed into.

`raml_parser.py` and `oas_parser.py` are two independent front-ends that both
produce the same `Operation` list plus a `DetectedSecurity` guess. Everything
downstream (auth.py, codegen.py) only ever sees this shared shape — it has no
idea whether the source spec was RAML or OpenAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Parameter:
    name: str
    location: str  # "path", "query", "header"
    required: bool
    schema: dict[str, Any]
    description: str = ""


@dataclass
class RequestBody:
    required: bool
    schema: dict[str, Any]
    content_type: str = "application/json"
    description: str = ""


@dataclass
class Operation:
    operation_id: str
    method: str  # lowercase: get/post/put/delete/patch/...
    path: str  # e.g. /orders/{orderId}
    summary: str = ""
    description: str = ""
    parameters: list[Parameter] = field(default_factory=list)
    request_body: RequestBody | None = None


@dataclass
class DetectedSecurity:
    """Best-effort guess at the spec's auth shape, used only for the CLI's
    informational output and for `--auth-mode auto`. Never trusted blindly —
    the user can always override with an explicit `--auth-mode`.
    """

    kind: str  # "client-id" | "oauth2" | "none" | "unknown"
    detail: str = ""
    client_id_header: str | None = None
    client_secret_header: str | None = None


@dataclass
class ParsedSpec:
    """Everything extracted from one spec document, regardless of source format."""

    format: str  # "raml" or "oas"
    title: str
    base_url: str
    operations: list[Operation]
    detected_security: DetectedSecurity


__all__ = [
    "DetectedSecurity",
    "Operation",
    "Parameter",
    "ParsedSpec",
    "RequestBody",
]
