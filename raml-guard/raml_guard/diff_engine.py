"""Classify the difference between two parsed RAML specs as breaking,
non-breaking, or informational.

This module is 100% deterministic: no network call, no LLM, no randomness.
Given the same two `RamlSpec` objects it always produces the same list of
`Change` objects in the same order.

## The rules, and why

These are the rules explicitly required by this project's spec, plus a
handful of reasonable extensions that follow the same logic (documented
inline at each rule). The guiding principle throughout: **a change is
"breaking" if a consumer built correctly against the OLD spec could send a
request or expect a response that the NEW spec's server would now reject or
no longer produce.**

Breaking:
- A resource or method is removed (a consumer's request has nowhere to go).
- A query/header/path parameter goes from optional to required (an old
  request that omitted it is now invalid).
- A NEW required parameter is added (same reasoning as above; an old
  request never included it -- not explicitly listed in the spec's example
  list, but it's the same principle as "optional -> required" applied to
  parameter addition, so it's implemented here too).
- A body/response object field is removed (a consumer reading/writing that
  field breaks).
- A body/response field's type is narrowed (e.g. string -> integer: not
  every old string value is a valid integer) or changes in an
  unrecognized/incompatible way (treated conservatively as breaking, since a
  CI gate should default to caution when it can't prove a change is safe).
- A request body field goes from optional to required (old payloads that
  omitted it now fail validation).
- A new REQUIRED field is added to a request body (old payloads never sent
  it).
- An enum value is removed from an accepted set (a value an old consumer
  might send/receive is no longer valid).
- A previously-supported response status code is removed (a consumer
  handling that status will never see it again).
- A security requirement is added where none existed (now requires auth
  that didn't before), or an additional security scheme is layered onto an
  already-secured resource/method.
- A request body media type is no longer accepted.

Non-breaking:
- A new resource or method is added.
- A new OPTIONAL parameter is added.
- A new field is added to a response body (regardless of whether it's
  marked required in the new schema -- "required" in a response schema
  means "the server promises to always include it", which doesn't break a
  consumer that was ignoring it before).
- A new enum value is added (widening the accepted/returned set).
- A new response status code is added.
- A body/response field's type is *widened* in a recognized-safe direction
  (integer -> number, integer -> string, number -> string, boolean ->
  string): every old value remains valid under the new, broader type.
- A parameter or request-body field goes from required to optional (old
  requests that always included it still work).
- Documentation/description-only text changes.
- A security requirement is removed outright (a resource/method becomes
  fully open) -- consumers that were sending credentials can simply keep
  sending them; nothing that used to work now fails.

Informational (a real, detected change, but not cleanly breaking or safe):
- A display name changes (cosmetic, but not "documentation text" in the
  same sense as `description`).
- A default value changes.
- A parameter/field is removed that wasn't a body/response object field
  (e.g. a query or header parameter) -- removing a query parameter usually
  doesn't break a consumer that keeps sending it (servers generally ignore
  unknown query params), but this tool can't prove that for your specific
  gateway, so it's flagged for a human to confirm rather than silently
  passed or aggressively failed.
- A response body field changes from required to optional -- the field is
  still there in the schema, but a consumer that assumed it was always
  present should double-check; this is a real behavioral change whose
  impact depends on how strict that consumer is, so it isn't auto-declared
  safe or unsafe.
- One security scheme is dropped from a multi-scheme `securedBy` list while
  at least one other still applies (the resource doesn't become fully open,
  but its auth requirements did change).
- An `!include` target changes to a different reference, or a schema's
  resolvability changes (was opaque, now inline, or vice versa) -- the
  content itself was never diffed in either case (see "Unresolved
  `!include` handling" below), so this is flagged rather than silently
  ignored or over-declared.
- A response body's documented media type is removed while the status
  code itself still exists.

## Unresolved `!include` handling

`raml_parser.py` resolves `!include` relative to the including file's own
directory when the target file exists and parses; when it can't (missing
file, parse error, path escapes what's readable, or recursion too deep) it
falls back to an **opaque** schema node (`{"type": "opaque", "ref": "..."}`)
instead of crashing or guessing at a shape. This module never fabricates a
diff about the *internals* of an opaque node -- see `_diff_schema` below,
which special-cases `type == "opaque"` and only ever compares the raw
reference string, emitting an informational note when it looks like it
changed and staying silent when it's unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from .raml_parser import MethodSpec, RamlSpec, ResourceSpec

BREAKING = "breaking"
NON_BREAKING = "non-breaking"
INFORMATIONAL = "informational"


@dataclass
class Change:
    category: str  # BREAKING | NON_BREAKING | INFORMATIONAL
    location: str  # e.g. "GET /orders/{id}" or "GET /orders query param 'status'"
    kind: str  # short machine-readable tag, e.g. "param-now-required"
    message: str  # one-line human-readable reason


# ---------------------------------------------------------------------------
# Type-change classification
# ---------------------------------------------------------------------------

# Pairs where every value of the OLD type is still a valid value of the NEW
# type -- i.e. the accepted/produced value space only grew. Anything not
# listed here (including the reverse of these pairs, and any pair involving
# "object"/"array") is treated as unknown and, conservatively, as narrowing.
_WIDENING_PAIRS = {
    ("integer", "number"),
    ("integer", "string"),
    ("number", "string"),
    ("boolean", "string"),
}
_NARROWING_PAIRS = {
    ("number", "integer"),
    ("string", "integer"),
    ("string", "number"),
    ("string", "boolean"),
}


def classify_type_change(old_type: str, new_type: str) -> str:
    """Return "same", "widening", "narrowing", or "unknown" for old_type -> new_type."""
    if old_type == new_type:
        return "same"
    if (old_type, new_type) in _WIDENING_PAIRS:
        return "widening"
    if (old_type, new_type) in _NARROWING_PAIRS:
        return "narrowing"
    return "unknown"


# ---------------------------------------------------------------------------
# Schema (body/response/param shape) diffing
# ---------------------------------------------------------------------------


def _diff_schema(old_schema: dict, new_schema: dict, location: str, role: str, changes: list) -> None:
    """Recursively diff two normalized schema shapes.

    ``role`` is "request" or "response" and changes how "field added
    required" and "field required-flag changed" are classified (see module
    docstring: the same structural change means something different
    depending on which side of the wire it's on).
    """
    if old_schema is None or new_schema is None:
        return

    old_type = old_schema.get("type", "string")
    new_type = new_schema.get("type", "string")

    if old_type == "opaque" or new_type == "opaque":
        old_ref = old_schema.get("ref")
        new_ref = new_schema.get("ref")
        if old_type == "opaque" and new_type == "opaque":
            if old_ref != new_ref:
                changes.append(
                    Change(
                        INFORMATIONAL,
                        location,
                        "opaque-ref-changed",
                        f"Referenced !include target changed ('{old_ref}' -> '{new_ref}'); "
                        "its contents were not resolved so internals were not diffed -- verify manually.",
                    )
                )
            # same ref, still opaque: no fabricated diff.
        else:
            changes.append(
                Change(
                    INFORMATIONAL,
                    location,
                    "opaque-resolution-changed",
                    "Schema resolvability changed between an opaque !include reference and an "
                    "inline/resolved shape; internals were not diffed either way -- verify manually.",
                )
            )
        return

    if old_type != new_type:
        kind = classify_type_change(old_type, new_type)
        if kind == "widening":
            changes.append(
                Change(
                    NON_BREAKING,
                    location,
                    "type-widened",
                    f"Type widened from '{old_type}' to '{new_type}' (every old value remains valid).",
                )
            )
        elif kind == "narrowing":
            changes.append(
                Change(
                    BREAKING,
                    location,
                    "type-narrowed",
                    f"Type narrowed from '{old_type}' to '{new_type}' -- not every old value is valid under the new type.",
                )
            )
        else:
            changes.append(
                Change(
                    BREAKING,
                    location,
                    "type-changed-incompatible",
                    f"Type changed from '{old_type}' to '{new_type}' in a way not recognized as a safe widening; "
                    "treated as breaking (conservative default for a CI gate).",
                )
            )
        # The base type itself changed -- don't try to diff properties/items
        # across two structurally different shapes.
        return

    old_enum = old_schema.get("enum")
    new_enum = new_schema.get("enum")
    if old_enum or new_enum:
        old_set = set(old_enum or [])
        new_set = set(new_enum or [])
        removed = old_set - new_set
        added = new_set - old_set
        if removed:
            changes.append(
                Change(
                    BREAKING,
                    location,
                    "enum-value-removed",
                    f"Enum value(s) removed from the accepted set: {sorted(removed)}.",
                )
            )
        if added:
            changes.append(
                Change(
                    NON_BREAKING,
                    location,
                    "enum-value-added",
                    f"Enum value(s) added, widening the accepted set: {sorted(added)}.",
                )
            )

    if old_schema.get("default") != new_schema.get("default"):
        changes.append(
            Change(
                INFORMATIONAL,
                location,
                "default-changed",
                f"Default value changed from {old_schema.get('default')!r} to {new_schema.get('default')!r}.",
            )
        )

    if old_type == "object":
        old_props = old_schema.get("properties") or {}
        new_props = new_schema.get("properties") or {}

        for name, old_prop in old_props.items():
            field_loc = f"{location}.{name}"
            if name not in new_props:
                changes.append(
                    Change(
                        BREAKING,
                        field_loc,
                        "field-removed",
                        f"Field '{name}' removed from the {role} body object schema.",
                    )
                )
                continue
            new_prop = new_props[name]
            old_req = bool(old_prop.get("required"))
            new_req = bool(new_prop.get("required"))
            if old_req != new_req:
                if new_req and not old_req:
                    if role == "response":
                        changes.append(
                            Change(
                                NON_BREAKING,
                                field_loc,
                                "field-now-required",
                                f"Field '{name}' in the response body is now always present (was optional); "
                                "the server making a stronger promise doesn't break existing consumers.",
                            )
                        )
                    else:
                        changes.append(
                            Change(
                                BREAKING,
                                field_loc,
                                "field-now-required",
                                f"Field '{name}' in the request body changed from optional to required; "
                                "existing clients that omit it will now fail validation.",
                            )
                        )
                else:
                    if role == "response":
                        changes.append(
                            Change(
                                INFORMATIONAL,
                                field_loc,
                                "field-now-optional",
                                f"Field '{name}' in the response body changed from required to optional; "
                                "consumers that assumed it was always present should verify.",
                            )
                        )
                    else:
                        changes.append(
                            Change(
                                NON_BREAKING,
                                field_loc,
                                "field-now-optional",
                                f"Field '{name}' in the request body changed from required to optional.",
                            )
                        )
            _diff_schema(old_prop, new_prop, field_loc, role, changes)

        for name, new_prop in new_props.items():
            if name in old_props:
                continue
            field_loc = f"{location}.{name}"
            is_required = bool(new_prop.get("required"))
            if role == "request" and is_required:
                changes.append(
                    Change(
                        BREAKING,
                        field_loc,
                        "field-added-required",
                        f"New required field '{name}' added to the request body; "
                        "existing clients built against the old schema won't send it.",
                    )
                )
            else:
                qualifier = " (optional)" if role == "request" else ""
                changes.append(
                    Change(
                        NON_BREAKING,
                        field_loc,
                        "field-added",
                        f"New field '{name}' added to the {role} body{qualifier}.",
                    )
                )
    elif old_type == "array":
        old_items = old_schema.get("items")
        new_items = new_schema.get("items")
        if old_items is not None and new_items is not None:
            _diff_schema(old_items, new_items, f"{location}[]", role, changes)


def _diff_params(old_params: dict, new_params: dict, loc_prefix: str, changes: list) -> None:
    for name, old_p in old_params.items():
        ploc = f"{loc_prefix} '{name}'"
        if name not in new_params:
            changes.append(
                Change(
                    INFORMATIONAL,
                    ploc,
                    "param-removed",
                    f"Parameter '{name}' was removed; if any consumer relies on it being documented/"
                    "validated server-side, verify manually.",
                )
            )
            continue
        new_p = new_params[name]
        if old_p.required != new_p.required:
            if new_p.required and not old_p.required:
                changes.append(
                    Change(
                        BREAKING,
                        ploc,
                        "param-now-required",
                        f"Parameter '{name}' changed from optional to required; "
                        "existing callers that omit it will now be rejected.",
                    )
                )
            else:
                changes.append(
                    Change(
                        NON_BREAKING,
                        ploc,
                        "param-now-optional",
                        f"Parameter '{name}' changed from required to optional.",
                    )
                )
        _diff_schema(old_p.schema, new_p.schema, ploc, "request", changes)

    for name, new_p in new_params.items():
        if name in old_params:
            continue
        ploc = f"{loc_prefix} '{name}'"
        if new_p.required:
            changes.append(
                Change(
                    BREAKING,
                    ploc,
                    "param-added-required",
                    f"New required parameter '{name}' added; existing callers built against the old spec won't send it.",
                )
            )
        else:
            changes.append(
                Change(
                    NON_BREAKING,
                    ploc,
                    "param-added",
                    f"New optional parameter '{name}' added.",
                )
            )


# ---------------------------------------------------------------------------
# Security resolution
# ---------------------------------------------------------------------------


def _effective_secured_by(spec: RamlSpec, resource: ResourceSpec, method: MethodSpec) -> list:
    """Resolve the effective securedBy list for one method, applying RAML's
    method -> resource -> root inheritance (first one that's explicitly set wins)."""
    if method.secured_by is not None:
        return method.secured_by
    if resource.secured_by is not None:
        return resource.secured_by
    if spec.secured_by is not None:
        return spec.secured_by
    return []


# ---------------------------------------------------------------------------
# Method-level diff
# ---------------------------------------------------------------------------


def _diff_method(
    old_spec: RamlSpec,
    new_spec: RamlSpec,
    old_resource: ResourceSpec,
    new_resource: ResourceSpec,
    old_m: MethodSpec,
    new_m: MethodSpec,
    loc: str,
    changes: list,
) -> None:
    if old_m.description != new_m.description and (old_m.description or new_m.description):
        changes.append(
            Change(NON_BREAKING, loc, "description-changed", "Description text changed (documentation-only).")
        )
    if old_m.display_name != new_m.display_name and old_m.display_name and new_m.display_name:
        changes.append(
            Change(
                INFORMATIONAL,
                loc,
                "display-name-changed",
                f"Method display name changed from '{old_m.display_name}' to '{new_m.display_name}'.",
            )
        )

    _diff_params(old_m.query_params, new_m.query_params, f"{loc} query param", changes)
    _diff_params(old_m.headers, new_m.headers, f"{loc} header", changes)

    old_sec = set(_effective_secured_by(old_spec, old_resource, old_m))
    new_sec = set(_effective_secured_by(new_spec, new_resource, new_m))
    added_sec = new_sec - old_sec
    removed_sec = old_sec - new_sec
    if added_sec and not old_sec:
        changes.append(
            Change(
                BREAKING,
                loc,
                "security-added",
                f"Endpoint now requires authentication ({sorted(added_sec)}) where none was required before.",
            )
        )
    elif added_sec:
        changes.append(
            Change(
                BREAKING,
                loc,
                "security-scheme-added",
                f"Additional security scheme(s) now required: {sorted(added_sec)}.",
            )
        )
    if removed_sec and not new_sec:
        changes.append(
            Change(
                NON_BREAKING,
                loc,
                "security-removed",
                f"Authentication requirement removed ({sorted(removed_sec)}); endpoint is now fully open.",
            )
        )
    elif removed_sec:
        changes.append(
            Change(
                INFORMATIONAL,
                loc,
                "security-scheme-removed",
                f"Security scheme(s) no longer required: {sorted(removed_sec)} (at least one other scheme still applies).",
            )
        )

    old_media = set(old_m.bodies)
    new_media = set(new_m.bodies)
    for mt in sorted(old_media - new_media):
        changes.append(
            Change(
                BREAKING,
                f"{loc} body[{mt}]",
                "request-media-type-removed",
                f"Request body media type '{mt}' is no longer accepted.",
            )
        )
    for mt in sorted(new_media - old_media):
        changes.append(
            Change(
                NON_BREAKING,
                f"{loc} body[{mt}]",
                "request-media-type-added",
                f"Request body media type '{mt}' is now accepted.",
            )
        )
    for mt in sorted(old_media & new_media):
        _diff_schema(old_m.bodies[mt].schema, new_m.bodies[mt].schema, f"{loc} body[{mt}]", "request", changes)

    old_status = set(old_m.responses)
    new_status = set(new_m.responses)
    for status in sorted(old_status - new_status):
        changes.append(
            Change(
                BREAKING,
                f"{loc} -> {status}",
                "response-status-removed",
                f"Response status code {status} was removed; consumers handling it will no longer see it.",
            )
        )
    for status in sorted(new_status - old_status):
        changes.append(
            Change(
                NON_BREAKING,
                f"{loc} -> {status}",
                "response-status-added",
                f"Response status code {status} was added.",
            )
        )
    for status in sorted(old_status & new_status):
        old_resp = old_m.responses[status]
        new_resp = new_m.responses[status]
        resp_media_old = set(old_resp.bodies)
        resp_media_new = set(new_resp.bodies)
        for mt in sorted(resp_media_old - resp_media_new):
            changes.append(
                Change(
                    INFORMATIONAL,
                    f"{loc} -> {status} body[{mt}]",
                    "response-media-type-removed",
                    f"Response media type '{mt}' for status {status} is no longer documented.",
                )
            )
        for mt in sorted(resp_media_new - resp_media_old):
            changes.append(
                Change(
                    NON_BREAKING,
                    f"{loc} -> {status} body[{mt}]",
                    "response-media-type-added",
                    f"Response media type '{mt}' for status {status} was added.",
                )
            )
        for mt in sorted(resp_media_old & resp_media_new):
            _diff_schema(
                old_resp.bodies[mt].schema, new_resp.bodies[mt].schema, f"{loc} -> {status} body[{mt}]", "response", changes
            )


# ---------------------------------------------------------------------------
# Top-level diff
# ---------------------------------------------------------------------------


def diff_specs(old: RamlSpec, new: RamlSpec) -> list:
    """Diff two parsed RAML specs and return a deterministic list of `Change`."""
    changes: list = []

    old_paths = set(old.resources)
    new_paths = set(new.resources)

    for path in sorted(old_paths - new_paths):
        old_resource = old.resources[path]
        if not old_resource.methods:
            changes.append(Change(BREAKING, path, "resource-removed", f"Resource '{path}' was removed entirely."))
        for verb in sorted(old_resource.methods):
            loc = f"{verb.upper()} {path}"
            changes.append(
                Change(
                    BREAKING,
                    loc,
                    "method-removed",
                    f"Resource '{path}' was removed entirely, eliminating {verb.upper()} {path}.",
                )
            )

    for path in sorted(new_paths - old_paths):
        new_resource = new.resources[path]
        if not new_resource.methods:
            changes.append(Change(NON_BREAKING, path, "resource-added", f"New (empty) resource '{path}' added."))
        for verb in sorted(new_resource.methods):
            loc = f"{verb.upper()} {path}"
            changes.append(
                Change(
                    NON_BREAKING,
                    loc,
                    "method-added",
                    f"New resource '{path}' added with {verb.upper()} {path}.",
                )
            )

    for path in sorted(old_paths & new_paths):
        old_resource = old.resources[path]
        new_resource = new.resources[path]

        if (
            old_resource.display_name != new_resource.display_name
            and old_resource.display_name
            and new_resource.display_name
        ):
            changes.append(
                Change(
                    INFORMATIONAL,
                    path,
                    "display-name-changed",
                    f"Resource display name changed from '{old_resource.display_name}' to '{new_resource.display_name}'.",
                )
            )

        _diff_params(old_resource.uri_params, new_resource.uri_params, f"{path} path param", changes)

        old_verbs = set(old_resource.methods)
        new_verbs = set(new_resource.methods)
        for verb in sorted(old_verbs - new_verbs):
            loc = f"{verb.upper()} {path}"
            changes.append(Change(BREAKING, loc, "method-removed", f"Method {verb.upper()} {path} was removed."))
        for verb in sorted(new_verbs - old_verbs):
            loc = f"{verb.upper()} {path}"
            changes.append(Change(NON_BREAKING, loc, "method-added", f"Method {verb.upper()} {path} was added."))

        for verb in sorted(old_verbs & new_verbs):
            loc = f"{verb.upper()} {path}"
            _diff_method(
                old,
                new,
                old_resource,
                new_resource,
                old_resource.methods[verb],
                new_resource.methods[verb],
                loc,
                changes,
            )

    return changes


__all__ = ["BREAKING", "NON_BREAKING", "INFORMATIONAL", "Change", "classify_type_change", "diff_specs"]
