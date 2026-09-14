"""Load and parse RAML 1.0 documents into the shared internal model.

RAML 1.0 structure (confirmed against https://raml.org/ and the RAML 1.0 spec
at https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md
before writing this file — see README "How it works" for a summary):

- The document MUST start with the literal line ``#%RAML 1.0``. Everything
  after that line is plain YAML.
- Root properties of interest: ``title``, ``baseUri``, ``version``,
  ``mediaType``, ``types`` (named, reusable type/schema definitions),
  ``securitySchemes`` (named auth-scheme definitions), and top-level
  ``securedBy``.
- Resources are dict keys starting with ``/`` and nest arbitrarily deeply
  (``/orders`` containing a nested ``/{orderId}``). Each resource level can
  declare ``uriParameters`` for any ``{placeholders}`` in its own segment.
- HTTP methods (``get``/``post``/``put``/``delete``/``patch``/``head``/
  ``options``) are dict keys directly under a resource. Each carries
  ``displayName``/``description``, ``queryParameters``, ``headers``,
  ``body`` (keyed by media type, e.g. ``application/json``), ``responses``,
  and ``securedBy``.
- ``queryParameters``/``headers``/``uriParameters``/``body.<mediaType>``
  properties support RAML's compact type shorthand (``name: string``) as
  well as the full form (``name: {type: string, required: false, ...}``),
  and RAML's "optional" suffix on the key itself (``name?: string``).
- Security schemes commonly seen on MuleSoft/Anypoint-managed APIs:
    - ``type: x-custom`` (or ``Pass Through``) with
      ``describedBy.headers`` listing two custom headers — this is the
      "Client ID enforcement" pattern (typically ``client_id``/
      ``client_secret``, or ``X-ANYPOINT-CLIENT-ID``/
      ``X-ANYPOINT-CLIENT-SECRET`` on Anypoint-managed APIs).
    - ``type: OAuth 2.0``.

Deliberately NOT supported (see README limitations):
- RAML modularity: ``!include``, ``uses:`` (libraries), ``traits``,
  ``resourceTypes``, multi-file specs. A RAML file must be self-contained.
- ``types:`` inheritance chains beyond one level of named-type lookup for a
  body/property (``type: Order`` resolves to ``types.Order`` if present;
  deeper composition is passed through best-effort, not fully resolved).
- Full XSD/JSON-Schema-in-RAML bodies (``schema:`` keyword, the RAML 0.8
  spelling) — only RAML 1.0's ``type:``/``properties:`` bodies are parsed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from mule_to_mcp.model import DetectedSecurity, Operation, Parameter, RequestBody

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")

_RAML_HEADER_RE = re.compile(r"^#%RAML\s+1\.0\b")

# RAML compact-type shorthand -> our internal "type" string. RAML also allows
# these as inline scalars for a whole property (`name: string`) rather than a
# `{type: string}` map.
_KNOWN_SCALAR_TYPES = {"string", "number", "integer", "boolean", "date-only", "datetime", "file", "nil"}


class RamlParseError(Exception):
    """Raised when a document is not a well-formed RAML 1.0 document."""


def looks_like_raml(raw_text: str) -> bool:
    """True if the first non-blank line is the RAML 1.0 header."""
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return bool(_RAML_HEADER_RE.match(stripped))
    return False


def load_raml(raw_text: str) -> dict[str, Any]:
    """Strip the ``#%RAML 1.0`` header line and parse the remainder as YAML."""
    if not looks_like_raml(raw_text):
        raise RamlParseError("Not a RAML 1.0 document: missing '#%RAML 1.0' header line")
    # Drop just the header line; keep everything else (including blank lines)
    # so YAML line numbers in any future error messages stay meaningful.
    lines = raw_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            lines[i] = ""  # blank out the header line itself
            break
    body = "\n".join(lines)
    try:
        doc = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise RamlParseError(f"Could not parse RAML body as YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise RamlParseError("Top-level RAML document must be a mapping/object")
    return doc


def load_raml_file(path_or_url: str, raw_text: str | None = None) -> dict[str, Any]:
    """Load a RAML document. ``raw_text`` lets callers pass already-fetched content."""
    if raw_text is None:
        raw_text = Path(path_or_url).read_text(encoding="utf-8")
    return load_raml(raw_text)


def _strip_optional_marker(key: str) -> tuple[str, bool]:
    """RAML marks an optional property/parameter with a trailing '?' on its key."""
    if key.endswith("?"):
        return key[:-1], False
    return key, True


def _named_type_schema(raw_value: Any, types: dict[str, Any]) -> dict[str, Any]:
    """Turn one property/parameter value (compact or full form) into a plain schema dict."""
    if isinstance(raw_value, str):
        # Compact form: `name: string` or `name: SomeNamedType` or `name: string[]`.
        type_name = raw_value
        is_array = type_name.endswith("[]")
        if is_array:
            type_name = type_name[:-2]
        base = _resolve_type_name(type_name, types)
        return {"type": "array", "items": base} if is_array else base
    if isinstance(raw_value, dict):
        type_name = raw_value.get("type")
        schema: dict[str, Any] = {}
        if isinstance(type_name, str):
            is_array = type_name.endswith("[]")
            if is_array:
                type_name = type_name[:-2]
            resolved = _resolve_type_name(type_name, types)
            if is_array:
                schema = {"type": "array", "items": resolved}
            else:
                schema = dict(resolved)
        elif "properties" in raw_value:
            schema = {"type": "object"}
        if "enum" in raw_value:
            schema["enum"] = raw_value["enum"]
        if "properties" in raw_value and isinstance(raw_value["properties"], dict):
            props_schema = _properties_to_schema(raw_value["properties"], types)
            schema["type"] = schema.get("type", "object")
            schema["properties"] = props_schema["properties"]
            if "required" in props_schema:
                schema["required"] = props_schema["required"]
        if "items" in raw_value and "items" not in schema:
            schema["type"] = "array"
            schema["items"] = _named_type_schema(raw_value["items"], types)
        if "default" in raw_value:
            schema["default"] = raw_value["default"]
        return schema or {"type": "string"}
    return {"type": "string"}


def _resolve_type_name(type_name: str, types: dict[str, Any]) -> dict[str, Any]:
    if type_name in _KNOWN_SCALAR_TYPES:
        json_type = "string" if type_name in ("date-only", "datetime", "file", "nil") else type_name
        return {"type": json_type}
    if type_name in ("object", "array"):
        return {"type": type_name}
    named = types.get(type_name)
    if named is not None:
        return _named_type_schema(named, types)
    # Unknown/unresolvable named type (e.g. defined in an !include'd library) —
    # fall back to an open object rather than failing the whole parse.
    return {"type": "object"}


def _properties_to_schema(raw_properties: dict[str, Any], types: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for raw_key, raw_value in raw_properties.items():
        key, required_default = _strip_optional_marker(str(raw_key))
        prop_schema = _named_type_schema(raw_value, types)
        props[key] = prop_schema
        explicit_required = raw_value.get("required") if isinstance(raw_value, dict) else None
        is_required = explicit_required if explicit_required is not None else required_default
        if is_required:
            required.append(key)
    result: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        result["required"] = required
    return result


def _parse_named_type_map(raw: dict[str, Any] | None, types: dict[str, Any], *, location: str) -> list[Parameter]:
    """Parse a RAML `queryParameters:`/`headers:`/`uriParameters:` block into Parameters."""
    params: list[Parameter] = []
    for raw_key, raw_value in (raw or {}).items():
        name, required_default = _strip_optional_marker(str(raw_key))
        schema = _named_type_schema(raw_value, types)
        explicit_required = raw_value.get("required") if isinstance(raw_value, dict) else None
        required = explicit_required if explicit_required is not None else required_default
        description = raw_value.get("description", "") if isinstance(raw_value, dict) else ""
        params.append(
            Parameter(
                name=name,
                location=location,
                required=bool(required) or location == "path",
                schema=schema,
                description=description,
            )
        )
    return params


def _extract_body(raw_method: dict[str, Any], types: dict[str, Any]) -> RequestBody | None:
    raw_body = raw_method.get("body")
    if not isinstance(raw_body, dict):
        return None
    # RAML bodies are keyed by media type, e.g. `application/json:`.
    # A body can also skip the media-type level and declare `type`/`properties`
    # directly (implied default mediaType) — handle both.
    if "properties" in raw_body or ("type" in raw_body and not any("/" in str(k) for k in raw_body)):
        media_type = "application/json"
        media = raw_body
    else:
        media_type = "application/json" if "application/json" in raw_body else next(iter(raw_body), "application/json")
        media = raw_body.get(media_type) or {}
        if not isinstance(media, dict):
            media = {}
    schema = _named_type_schema(media, types) if media else {"type": "object"}
    return RequestBody(
        required=True,
        schema=schema,
        content_type=media_type,
        description=raw_method.get("description", "") if isinstance(raw_method.get("description"), str) else "",
    )


def _walk_resources(
    node: dict[str, Any],
    prefix: str,
    inherited_uri_params: list[Parameter],
    types: dict[str, Any],
    operations: list[Operation],
) -> None:
    for key, value in node.items():
        if not isinstance(key, str) or not key.startswith("/") or not isinstance(value, dict):
            continue
        full_path = prefix + key
        own_uri_params = _parse_named_type_map(value.get("uriParameters"), types, location="path")
        # Any {placeholder} in this segment without an explicit uriParameters
        # entry still becomes a required string path parameter.
        declared_names = {p.name for p in own_uri_params}
        for placeholder in re.findall(r"\{([^}]+)\}", key):
            if placeholder not in declared_names:
                own_uri_params.append(
                    Parameter(name=placeholder, location="path", required=True, schema={"type": "string"})
                )
        all_uri_params = inherited_uri_params + own_uri_params

        for method in HTTP_METHODS:
            raw_method = value.get(method)
            if not isinstance(raw_method, dict):
                continue
            query_params = _parse_named_type_map(raw_method.get("queryParameters"), types, location="query")
            header_params = _parse_named_type_map(raw_method.get("headers"), types, location="header")
            # RAML has no operationId, but a method-level `displayName` (e.g. "List Orders")
            # plays the same role and is unique per operation -- use it as the tool-name
            # source (codegen snake_cases it), falling back to method+path synthesis when
            # a method has no displayName of its own. The resource-level displayName (if
            # any) is only used as a fallback for the human-readable summary/description.
            method_display_name = raw_method.get("displayName") or ""
            display_name = method_display_name or value.get("displayName") or ""
            description = raw_method.get("description") or ""
            operations.append(
                Operation(
                    operation_id=str(method_display_name),
                    method=method,
                    path=full_path,
                    summary=str(display_name),
                    description=str(description),
                    parameters=[*all_uri_params, *query_params, *header_params],
                    request_body=_extract_body(raw_method, types),
                )
            )

        _walk_resources(value, full_path, all_uri_params, types, operations)


def extract_operations(doc: dict[str, Any]) -> list[Operation]:
    """Flatten a parsed RAML document into one Operation per (path, method)."""
    types = doc.get("types") or {}
    operations: list[Operation] = []
    _walk_resources(doc, "", [], types, operations)
    return operations


def get_base_url(doc: dict[str, Any]) -> str:
    base_uri = doc.get("baseUri")
    if base_uri:
        # RAML allows `{version}` templating in baseUri; substitute if `version` is set.
        version = doc.get("version")
        if version and "{version}" in str(base_uri):
            base_uri = str(base_uri).replace("{version}", str(version))
        return str(base_uri).rstrip("/")
    return "http://localhost:8000"


def get_api_title(doc: dict[str, Any]) -> str:
    return str(doc.get("title") or "Generated API")


def _looks_like_client_id_pair(header_names: list[str]) -> tuple[str, str] | None:
    """If exactly two headers look like a client-id/client-secret pair, return (id_header, secret_header)."""
    lowered = {h.lower(): h for h in header_names}
    id_candidates = [orig for low, orig in lowered.items() if "client" in low and "id" in low]
    secret_candidates = [orig for low, orig in lowered.items() if "client" in low and "secret" in low]
    if id_candidates and secret_candidates:
        return id_candidates[0], secret_candidates[0]
    return None


def detect_security(doc: dict[str, Any]) -> DetectedSecurity:
    """Best-effort detection of the RAML document's auth shape from `securitySchemes`."""
    schemes = doc.get("securitySchemes") or {}
    if not isinstance(schemes, dict) or not schemes:
        return DetectedSecurity(kind="none", detail="No securitySchemes declared in the RAML document.")

    for scheme_name, scheme in schemes.items():
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type", ""))
        described_by = scheme.get("describedBy") or {}
        headers = list((described_by.get("headers") or {}).keys()) if isinstance(described_by, dict) else []
        header_names = [_strip_optional_marker(str(h))[0] for h in headers]

        if scheme_type.strip().lower() == "oauth 2.0":
            return DetectedSecurity(
                kind="oauth2",
                detail=f"RAML securityScheme '{scheme_name}' has type 'OAuth 2.0'.",
            )

        pair = _looks_like_client_id_pair(header_names)
        if pair:
            return DetectedSecurity(
                kind="client-id",
                detail=(
                    f"RAML securityScheme '{scheme_name}' (type '{scheme_type}') declares custom headers "
                    f"{header_names!r} matching the Client ID enforcement pattern."
                ),
                client_id_header=pair[0],
                client_secret_header=pair[1],
            )

    return DetectedSecurity(
        kind="unknown",
        detail=f"securitySchemes present ({list(schemes)!r}) but none matched a known pattern.",
    )


__all__ = [
    "RamlParseError",
    "detect_security",
    "extract_operations",
    "get_api_title",
    "get_base_url",
    "load_raml",
    "load_raml_file",
    "looks_like_raml",
]
