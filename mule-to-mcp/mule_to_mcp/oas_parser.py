"""Load and parse OpenAPI 3.x documents into the shared internal model.

Adapted from the sibling project `openapi-to-mcp`'s `parser.py` (same proven
approach: local + URL loading, local `#/...` `$ref` resolution with cycle
protection, one `Operation` per (path, method)) — retargeted here to produce
`mule_to_mcp.model.Operation` so both RAML and OpenAPI specs feed the exact
same codegen path. Also adds OAS `securitySchemes`/`security` detection so
mule-to-mcp can offer the same Client-ID/OAuth2 auto-detection for OpenAPI
specs that describe Anypoint-managed / Mule-fronted APIs (OAS is a legal
input format alongside RAML here, not just RAML).

Deliberately NOT supported (matches openapi-to-mcp's documented limitations):
- External ($ref to another file / URL) references.
- `allOf`/`oneOf`/`anyOf` composition beyond simple `$ref` resolution.
- OpenAPI 2.0 (Swagger) documents.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from mule_to_mcp.model import DetectedSecurity, Operation, Parameter, RequestBody

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class OASParseError(Exception):
    """Raised when a document cannot be loaded or is not a supported OpenAPI version."""


def _is_url(spec_path: str) -> bool:
    return re.match(r"^https?://", spec_path, re.IGNORECASE) is not None


def looks_like_oas(raw_text: str) -> bool:
    """Best-effort sniff: does this look like an OpenAPI/Swagger document (not RAML)?"""
    try:
        doc = yaml.safe_load(raw_text)
    except yaml.YAMLError:
        return False
    if not isinstance(doc, dict):
        return False
    return "openapi" in doc or "swagger" in doc


def fetch_text(path_or_url: str) -> str:
    """Read raw text from a local file path or an http(s) URL."""
    if _is_url(path_or_url):
        with urllib.request.urlopen(path_or_url, timeout=30) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    p = Path(path_or_url)
    if not p.is_file():
        raise OASParseError(f"Spec file not found: {path_or_url}")
    return p.read_text(encoding="utf-8")


def load_spec(path_or_url: str, raw_text: str | None = None) -> dict[str, Any]:
    """Load a raw OpenAPI document (dict) from a local path/URL, or from already-fetched text."""
    raw = raw_text if raw_text is not None else fetch_text(path_or_url)
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise OASParseError(f"Could not parse spec as JSON/YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise OASParseError("Top-level OpenAPI document must be a mapping/object")

    version = str(doc.get("openapi", ""))
    if not version.startswith("3."):
        raise OASParseError(
            f"Only OpenAPI 3.x documents are supported (found openapi: {version!r}). "
            "Swagger 2.0 ('swagger: 2.0') is not supported."
        )
    return doc


def resolve_ref(ref: str, root: dict[str, Any]) -> Any:
    if not ref.startswith("#/"):
        raise OASParseError(f"Only local '#/...' $ref pointers are supported, got: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise OASParseError(f"Could not resolve $ref {ref!r}: missing segment {part!r}")
        node = node[part]
    return node


def resolve_schema(schema: Any, root: dict[str, Any], _seen: frozenset[str] = frozenset()) -> Any:
    if isinstance(schema, dict):
        if "$ref" in schema and isinstance(schema["$ref"], str):
            ref = schema["$ref"]
            if ref in _seen:
                return {"$ref": ref}
            resolved = resolve_ref(ref, root)
            return resolve_schema(resolved, root, _seen | {ref})
        return {k: resolve_schema(v, root, _seen) for k, v in schema.items()}
    if isinstance(schema, list):
        return [resolve_schema(item, root, _seen) for item in schema]
    return schema


def _merge_parameters(
    path_level: list[dict[str, Any]], op_level: list[dict[str, Any]], root: dict[str, Any]
) -> list[Parameter]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in path_level + op_level:
        raw = resolve_schema(raw, root)
        key = (raw.get("name", ""), raw.get("in", ""))
        merged[key] = raw

    params: list[Parameter] = []
    for (name, location), raw in merged.items():
        if location not in ("path", "query", "header"):
            continue
        schema = raw.get("schema") or {}
        if not schema and "content" in raw:
            content = raw["content"] or {}
            first_media = next(iter(content.values()), {})
            schema = first_media.get("schema") or {}
        params.append(
            Parameter(
                name=name,
                location=location,
                required=bool(raw.get("required", location == "path")),
                schema=schema,
                description=raw.get("description", ""),
            )
        )
    return params


def _extract_request_body(raw_op: dict[str, Any], root: dict[str, Any]) -> RequestBody | None:
    raw_body = raw_op.get("requestBody")
    if not raw_body:
        return None
    raw_body = resolve_schema(raw_body, root)
    content = raw_body.get("content") or {}
    content_type = "application/json" if "application/json" in content else next(iter(content), "application/json")
    media = content.get(content_type, {})
    schema = media.get("schema") or {"type": "object"}
    return RequestBody(
        required=bool(raw_body.get("required", False)),
        schema=schema,
        content_type=content_type,
        description=raw_body.get("description", ""),
    )


def extract_operations(spec: dict[str, Any]) -> list[Operation]:
    operations: list[Operation] = []
    paths = spec.get("paths") or {}
    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_item = resolve_schema(path_item, spec)
        path_level_params = path_item.get("parameters") or []
        for method in HTTP_METHODS:
            raw_op = path_item.get(method)
            if not raw_op:
                continue
            op_level_params = raw_op.get("parameters") or []
            operations.append(
                Operation(
                    operation_id=raw_op.get("operationId", ""),
                    method=method,
                    path=raw_path,
                    summary=raw_op.get("summary", ""),
                    description=raw_op.get("description", ""),
                    parameters=_merge_parameters(path_level_params, op_level_params, spec),
                    request_body=_extract_request_body(raw_op, spec),
                )
            )
    return operations


def get_base_url(spec: dict[str, Any]) -> str:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        return str(servers[0]["url"]).rstrip("/")
    return "http://localhost:8000"


def get_api_title(spec: dict[str, Any]) -> str:
    info = spec.get("info") or {}
    return str(info.get("title") or "Generated API")


def detect_security(spec: dict[str, Any]) -> DetectedSecurity:
    """Best-effort detection of the OAS document's auth shape.

    Looks at `components.securitySchemes`: an `apiKey`-in-header pair of
    client-id/secret-looking header names is treated like RAML's Client ID
    enforcement pattern; `oauth2` or `http`/`bearer` is treated as OAuth2.
    """
    schemes = ((spec.get("components") or {}).get("securitySchemes")) or {}
    if not isinstance(schemes, dict) or not schemes:
        return DetectedSecurity(kind="none", detail="No components.securitySchemes declared in the OpenAPI document.")

    header_like: list[tuple[str, str]] = []
    for scheme_name, scheme in schemes.items():
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type", "")).lower()
        if scheme_type in ("oauth2", "http") or (scheme_type == "http" and scheme.get("scheme") == "bearer"):
            return DetectedSecurity(kind="oauth2", detail=f"OAS securityScheme '{scheme_name}' has type '{scheme_type}'.")
        if scheme_type == "apikey" and str(scheme.get("in", "")).lower() == "header":
            header_like.append((scheme_name, str(scheme.get("name", ""))))

    if len(header_like) >= 2:
        lowered = {name.lower(): (sname, name) for sname, name in header_like}
        id_match = next((v for k, v in lowered.items() if "client" in k and "id" in k), None)
        secret_match = next((v for k, v in lowered.items() if "client" in k and "secret" in k), None)
        if id_match and secret_match:
            return DetectedSecurity(
                kind="client-id",
                detail=f"OAS apiKey securitySchemes {[h for _, h in header_like]!r} match the Client ID enforcement pattern.",
                client_id_header=id_match[1],
                client_secret_header=secret_match[1],
            )

    return DetectedSecurity(kind="unknown", detail=f"securitySchemes present ({list(schemes)!r}) but none matched a known pattern.")


__all__ = [
    "OASParseError",
    "detect_security",
    "extract_operations",
    "fetch_text",
    "get_api_title",
    "get_base_url",
    "load_spec",
    "looks_like_oas",
    "resolve_ref",
    "resolve_schema",
]
