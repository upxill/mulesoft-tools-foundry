"""A self-contained RAML 1.0 parser.

RAML 1.0 structure (confirmed against https://raml.org/ and the RAML 1.0
spec at https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md,
and cross-checked against the parsing patterns already used by a sibling
project, mule-to-mcp's ``raml_parser.py``, though this file is written fresh
and self-contained -- it does not import that project):

- The document MUST start with the literal line ``#%RAML 1.0``. Everything
  after that line is plain YAML.
- Root properties of interest here: ``title``, ``baseUri``, ``version``,
  ``types`` (named, reusable type/schema definitions), ``securitySchemes``
  (named auth-scheme definitions), and top-level ``securedBy``.
- Resources are dict keys starting with ``/`` and nest arbitrarily deeply
  (``/orders`` containing a nested ``/{orderId}``). Each resource level can
  declare ``uriParameters`` for any ``{placeholders}`` in its own segment,
  and can itself carry a ``securedBy`` that applies to all of its methods
  unless a method overrides it.
- HTTP methods (``get``/``post``/``put``/``delete``/``patch``/``head``/
  ``options``) are dict keys directly under a resource. Each carries
  ``displayName``/``description``, ``queryParameters``, ``headers``,
  ``body`` (keyed by media type, e.g. ``application/json``), ``responses``
  (keyed by status code, each with its own ``body``), and ``securedBy``.
- ``queryParameters``/``headers``/``uriParameters``/``body.<mediaType>``
  properties support RAML's compact type shorthand (``name: string``), the
  full form (``name: {type: string, required: false, ...}``), and RAML's
  "optional" suffix on the key itself (``name?: string``).
- ``!include`` is YAML's custom-tag mechanism for pulling in an external
  file (a type definition, a JSON Schema fragment, a trait, etc). PyYAML has
  no built-in constructor for it, so this module registers one
  (``_IncludeRef``) and resolves it relative to the *including file's own
  directory* -- per file, since a nested include's own further includes must
  resolve relative to *its* directory, not the original entry file's.

What this parser deliberately does NOT fully implement (see README
"Limitations"):
- RAML ``traits`` and ``resourceTypes`` (structural inheritance/mixins) are
  not expanded; a method that only exists via a ``resourceType`` won't be
  seen. Most hand-written governance-relevant RAML in the wild declares
  methods directly on resources, which is what this parser targets.
- ``uses:`` (RAML libraries) are not resolved as a distinct construct; a
  type referenced through a library prefix (``LibName.TypeName``) falls back
  to an open ``object`` shape rather than being fully resolved, the same
  fallback used for any other unresolvable named type.
- Only ``type:``/``properties:`` (RAML 1.0 native) body shapes are parsed
  for structural diffing. The RAML 0.8 ``schema:`` keyword (raw XSD/JSON
  Schema text) is recorded as an opaque string blob, not diffed field by
  field.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")

_RAML_HEADER_RE = re.compile(r"^#%RAML\s+1\.0\b")

# RAML's built-in scalar type names. Anything not in here and not "object"/
# "array" is either a named type (looked up in `types:`) or, if that lookup
# fails too, treated as an open object as a safe fallback.
_KNOWN_SCALAR_TYPES = {
    "string",
    "number",
    "integer",
    "boolean",
    "date-only",
    "time-only",
    "datetime-only",
    "datetime",
    "file",
    "nil",
    "any",
}


class RamlParseError(Exception):
    """Raised when a document is not a well-formed, parseable RAML 1.0 document."""


# ---------------------------------------------------------------------------
# !include support
# ---------------------------------------------------------------------------


class _IncludeRef:
    """A placeholder for an unresolved ``!include <path>`` YAML node."""

    __slots__ = ("raw_path",)

    def __init__(self, raw_path: str) -> None:
        self.raw_path = raw_path

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"IncludeRef({self.raw_path!r})"


def _make_loader() -> type:
    """Build a fresh SafeLoader subclass with an ``!include`` constructor.

    A fresh subclass is used per call (not a single shared one) so that
    registering the constructor is side-effect-free with respect to any
    other code in the process using plain ``yaml.SafeLoader``.
    """

    class _Loader(yaml.SafeLoader):
        pass

    def _construct_include(loader: yaml.SafeLoader, node: yaml.Node) -> _IncludeRef:
        return _IncludeRef(loader.construct_scalar(node))

    _Loader.add_constructor("!include", _construct_include)
    return _Loader


def looks_like_raml(raw_text: str) -> bool:
    """True if the first non-blank line is the RAML 1.0 header."""
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return bool(_RAML_HEADER_RE.match(stripped))
    return False


def _blank_out_header(raw_text: str) -> str:
    """Return the document text with just its ``#%RAML 1.0`` header line blanked."""
    lines = raw_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            if _RAML_HEADER_RE.match(line.strip()):
                lines[i] = ""
            break
    return "\n".join(lines)


def _load_yaml_text(text: str) -> Any:
    return yaml.load(text, Loader=_make_loader())


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Parameter:
    """A query/header/path parameter."""

    name: str
    required: bool
    schema: dict = field(default_factory=lambda: {"type": "string"})
    description: str = ""

    @property
    def type(self) -> str:
        return self.schema.get("type", "string")

    @property
    def enum(self) -> Optional[list]:
        return self.schema.get("enum")


@dataclass
class Body:
    """A request or response body for one media type."""

    media_type: str
    schema: dict


@dataclass
class ResponseSpec:
    status: str
    bodies: dict = field(default_factory=dict)  # media_type -> Body
    description: str = ""


@dataclass
class MethodSpec:
    verb: str
    display_name: str = ""
    description: str = ""
    query_params: dict = field(default_factory=dict)  # name -> Parameter
    headers: dict = field(default_factory=dict)  # name -> Parameter
    bodies: dict = field(default_factory=dict)  # media_type -> Body (request)
    responses: dict = field(default_factory=dict)  # status -> ResponseSpec
    secured_by: Optional[list] = None  # None = "not specified here", inherits


@dataclass
class ResourceSpec:
    path: str
    display_name: str = ""
    uri_params: dict = field(default_factory=dict)  # name -> Parameter
    methods: dict = field(default_factory=dict)  # verb -> MethodSpec
    secured_by: Optional[list] = None


@dataclass
class RamlSpec:
    source_path: str
    title: str = ""
    version: str = ""
    base_uri: str = ""
    resources: dict = field(default_factory=dict)  # full path -> ResourceSpec
    security_schemes: dict = field(default_factory=dict)
    secured_by: Optional[list] = None
    unresolved_includes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Type-shape resolution (handles compact/full forms, named types, !include)
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    base_dir: Path
    types: dict
    unresolved: list
    depth_limit: int = 10
    resolving_types: set = field(default_factory=set)


def _strip_optional_marker(key: str) -> tuple:
    if key.endswith("?"):
        return key[:-1], False
    return key, True


def _json_schema_to_shape(data: Any) -> dict:
    """Convert a plain JSON Schema fragment (from an !include'd .json file) to our shape."""
    if not isinstance(data, dict):
        return {"type": "string"}
    schema: dict = {"type": data.get("type", "object")}
    if "enum" in data and isinstance(data["enum"], list):
        schema["enum"] = list(data["enum"])
    if "default" in data:
        schema["default"] = data["default"]
    if "properties" in data and isinstance(data["properties"], dict):
        required_names = set(data.get("required") or [])
        props = {}
        for key, val in data["properties"].items():
            prop_shape = _json_schema_to_shape(val)
            prop_shape["required"] = key in required_names
            props[str(key)] = prop_shape
        schema["type"] = schema.get("type", "object")
        schema["properties"] = props
    if "items" in data:
        schema["type"] = "array"
        schema["items"] = _json_schema_to_shape(data["items"])
    return schema


def _resolve_include(ref: _IncludeRef, ctx: _Ctx, depth: int) -> dict:
    raw_path = ref.raw_path
    if depth >= ctx.depth_limit:
        ctx.unresolved.append(raw_path)
        return {"type": "opaque", "ref": raw_path}
    candidate = ctx.base_dir / raw_path
    try:
        if not candidate.is_file():
            raise FileNotFoundError(raw_path)
        text = candidate.read_text(encoding="utf-8")
        if candidate.suffix.lower() == ".json":
            data = json.loads(text)
            return _json_schema_to_shape(data)
        body = _blank_out_header(text) if looks_like_raml(text) else text
        doc = _load_yaml_text(body)
        nested_ctx = _Ctx(
            base_dir=candidate.parent,
            types=ctx.types,
            unresolved=ctx.unresolved,
            depth_limit=ctx.depth_limit,
            resolving_types=ctx.resolving_types,
        )
        return _type_shape(doc, nested_ctx, depth + 1)
    except Exception:
        # File missing, unreadable, unparseable, or too deep -- fall back to an
        # opaque reference rather than crashing or fabricating a shape. The
        # diff engine knows to never synthesize a diff about an opaque
        # reference's internals (see diff_engine._diff_schema).
        ctx.unresolved.append(raw_path)
        return {"type": "opaque", "ref": raw_path}


def _resolve_named_type(type_name: str, ctx: _Ctx, depth: int) -> dict:
    is_array = type_name.endswith("[]")
    if is_array:
        type_name = type_name[:-2]

    if type_name in _KNOWN_SCALAR_TYPES:
        json_type = (
            "string"
            if type_name in ("date-only", "time-only", "datetime-only", "datetime", "file", "nil", "any")
            else type_name
        )
        base = {"type": json_type}
    elif type_name in ("object", "array"):
        base = {"type": type_name}
    elif type_name in ctx.types:
        if type_name in ctx.resolving_types:
            # Self-referential / cyclic named type -- break the cycle rather
            # than recursing forever. Best-effort: treat as an open object.
            base = {"type": "object"}
        else:
            ctx.resolving_types.add(type_name)
            try:
                base = _type_shape(ctx.types[type_name], ctx, depth + 1)
            finally:
                ctx.resolving_types.discard(type_name)
    else:
        # Unknown named type (e.g. defined in an unresolved library, or a
        # typo) -- fall back to an open object rather than failing the parse.
        base = {"type": "object"}

    if is_array:
        return {"type": "array", "items": base}
    return base


def _type_shape(value: Any, ctx: _Ctx, depth: int = 0) -> dict:
    if depth > ctx.depth_limit:
        return {"type": "opaque", "ref": "max-include-depth-exceeded"}

    if isinstance(value, _IncludeRef):
        return _resolve_include(value, ctx, depth)

    if isinstance(value, str):
        return _resolve_named_type(value, ctx, depth)

    if isinstance(value, dict):
        schema: dict = {}
        type_field = value.get("type")

        if isinstance(type_field, str):
            schema.update(_resolve_named_type(type_field, ctx, depth))
        elif isinstance(type_field, _IncludeRef):
            schema.update(_resolve_include(type_field, ctx, depth))
        elif isinstance(type_field, list):
            # RAML allows multiple-inheritance style `type: [A, B]`; we only
            # take the first as a best-effort base shape.
            if type_field:
                schema.update(_type_shape(type_field[0], ctx, depth + 1))
        elif "properties" in value:
            schema["type"] = "object"

        if "enum" in value and isinstance(value["enum"], list):
            schema["enum"] = list(value["enum"])

        if "properties" in value and isinstance(value["properties"], dict):
            props: dict = {}
            for raw_key, raw_val in value["properties"].items():
                key, required_default = _strip_optional_marker(str(raw_key))
                prop_schema = dict(_type_shape(raw_val, ctx, depth + 1))
                explicit_required = raw_val.get("required") if isinstance(raw_val, dict) else None
                is_required = explicit_required if explicit_required is not None else required_default
                prop_schema["required"] = bool(is_required)
                props[key] = prop_schema
            schema["type"] = schema.get("type", "object")
            schema["properties"] = props

        if "items" in value and "items" not in schema:
            schema["type"] = "array"
            schema["items"] = _type_shape(value["items"], ctx, depth + 1)

        if "default" in value:
            schema["default"] = value["default"]

        return schema or {"type": "string"}

    if value is None:
        return {"type": "string"}

    return {"type": "string"}


# ---------------------------------------------------------------------------
# Parameter / body / response / security parsing
# ---------------------------------------------------------------------------


def _parse_param_map(raw: Any, ctx: _Ctx, location: str) -> dict:
    result: dict = {}
    if not isinstance(raw, dict):
        return result
    for raw_key, raw_val in raw.items():
        name, required_default = _strip_optional_marker(str(raw_key))
        schema = dict(_type_shape(raw_val, ctx))
        schema.pop("required", None)  # only meaningful nested inside object properties
        explicit_required = raw_val.get("required") if isinstance(raw_val, dict) else None
        required = explicit_required if explicit_required is not None else required_default
        description = raw_val.get("description", "") if isinstance(raw_val, dict) else ""
        result[name] = Parameter(
            name=name,
            required=bool(required) or location == "path",
            schema=schema,
            description=str(description or ""),
        )
    return result


def _parse_bodies(raw_body: Any, ctx: _Ctx) -> dict:
    bodies: dict = {}
    if not isinstance(raw_body, dict):
        return bodies
    keys = list(raw_body.keys())
    looks_direct = ("properties" in raw_body) or (
        "type" in raw_body and not any("/" in str(k) for k in keys)
    )
    if looks_direct:
        bodies["application/json"] = Body(media_type="application/json", schema=_type_shape(raw_body, ctx))
    else:
        for media_type, media_val in raw_body.items():
            if not isinstance(media_val, (dict, _IncludeRef)):
                continue
            bodies[str(media_type)] = Body(media_type=str(media_type), schema=_type_shape(media_val, ctx))
    return bodies


def _parse_responses(raw_responses: Any, ctx: _Ctx) -> dict:
    result: dict = {}
    if not isinstance(raw_responses, dict):
        return result
    for status, raw_resp in raw_responses.items():
        status_str = str(status)
        if not isinstance(raw_resp, dict):
            raw_resp = {}
        bodies = _parse_bodies(raw_resp.get("body"), ctx)
        result[status_str] = ResponseSpec(
            status=status_str,
            bodies=bodies,
            description=str(raw_resp.get("description") or ""),
        )
    return result


def _parse_secured_by(raw: Any) -> Optional[list]:
    """Normalize a RAML `securedBy:` value to a sorted list of scheme names.

    Returns ``None`` if the key is absent (meaning "not specified here";
    callers fall back to the parent's value). Returns ``[]`` if the value
    is present but resolves to "no security" (an empty list, or a list
    containing only ``null``, which is RAML's way of explicitly overriding
    an inherited securedBy with "none").
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raw = [raw]
    names = []
    for item in raw:
        if item is None:
            continue
        elif isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.extend(str(k) for k in item.keys())
    return names


# ---------------------------------------------------------------------------
# Resource tree walk
# ---------------------------------------------------------------------------


def _walk_resources(node: dict, prefix: str, inherited_uri_params: dict, ctx: _Ctx, resources: dict) -> None:
    for key, value in node.items():
        if not isinstance(key, str) or not key.startswith("/") or not isinstance(value, dict):
            continue
        full_path = prefix + key
        own_uri_params = _parse_param_map(value.get("uriParameters"), ctx, "path")
        declared = set(own_uri_params)
        for placeholder in re.findall(r"\{([^}]+)\}", key):
            if placeholder not in declared:
                own_uri_params[placeholder] = Parameter(name=placeholder, required=True, schema={"type": "string"})
        all_uri_params = {**inherited_uri_params, **own_uri_params}

        resource = ResourceSpec(
            path=full_path,
            display_name=str(value.get("displayName") or ""),
            uri_params=dict(all_uri_params),
            secured_by=_parse_secured_by(value.get("securedBy")),
        )

        for method in HTTP_METHODS:
            raw_method = value.get(method)
            if not isinstance(raw_method, dict):
                continue
            query_params = _parse_param_map(raw_method.get("queryParameters"), ctx, "query")
            headers = _parse_param_map(raw_method.get("headers"), ctx, "header")
            bodies = _parse_bodies(raw_method.get("body"), ctx)
            responses = _parse_responses(raw_method.get("responses"), ctx)
            secured_by = _parse_secured_by(raw_method.get("securedBy"))
            resource.methods[method] = MethodSpec(
                verb=method,
                display_name=str(raw_method.get("displayName") or ""),
                description=str(raw_method.get("description") or ""),
                query_params=query_params,
                headers=headers,
                bodies=bodies,
                responses=responses,
                secured_by=secured_by,
            )

        resources[full_path] = resource
        _walk_resources(value, full_path, all_uri_params, ctx, resources)


def parse_raml_text(raw_text: str, source_path: str, base_dir: Optional[Path] = None) -> RamlSpec:
    """Parse already-read RAML 1.0 text. ``base_dir`` anchors relative !include paths."""
    if not looks_like_raml(raw_text):
        raise RamlParseError(f"{source_path}: not a RAML 1.0 document (missing '#%RAML 1.0' header line)")
    body = _blank_out_header(raw_text)
    try:
        doc = _load_yaml_text(body)
    except yaml.YAMLError as exc:
        raise RamlParseError(f"{source_path}: could not parse RAML body as YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise RamlParseError(f"{source_path}: top-level RAML document must be a mapping")

    resolved_base_dir = base_dir if base_dir is not None else Path(".")
    types_raw = doc.get("types") or {}
    unresolved: list = []
    ctx = _Ctx(base_dir=resolved_base_dir, types=types_raw, unresolved=unresolved)

    resources: dict = {}
    _walk_resources(doc, "", {}, ctx, resources)

    return RamlSpec(
        source_path=source_path,
        title=str(doc.get("title") or ""),
        version=str(doc.get("version") or ""),
        base_uri=str(doc.get("baseUri") or ""),
        resources=resources,
        security_schemes=doc.get("securitySchemes") or {},
        secured_by=_parse_secured_by(doc.get("securedBy")),
        unresolved_includes=unresolved,
    )


def parse_raml_file(path: str) -> RamlSpec:
    """Parse a RAML 1.0 file from disk, resolving ``!include`` relative to its directory."""
    p = Path(path)
    raw_text = p.read_text(encoding="utf-8")
    return parse_raml_text(raw_text, str(path), base_dir=p.parent)


__all__ = [
    "Body",
    "MethodSpec",
    "Parameter",
    "RamlParseError",
    "RamlSpec",
    "ResourceSpec",
    "ResponseSpec",
    "HTTP_METHODS",
    "looks_like_raml",
    "parse_raml_file",
    "parse_raml_text",
]
