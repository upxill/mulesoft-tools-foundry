"""Render a standalone, runnable MCP server project from parsed operations.

Same design choice as the sibling `openapi-to-mcp` project: plain f-string
templating instead of Jinja2 -- the generated server is a handful of small,
regular, line-per-tool code blocks, and keeping this generator's own
dependency list tiny (pyyaml + requests) keeps `pip install -e .` fast.

The MCP server pattern used here (`from mcp.server import MCPServer`,
`@mcp.tool(name=..., description=...)`, `mcp.run()`) was directly confirmed
against the installed `mcp` package (v2.2.0) via `inspect.signature` before
writing this file, and is the same confirmed-correct pattern already
verified live in openapi-to-mcp/openapi_to_mcp/codegen.py -- reused here
rather than re-derived.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mule_to_mcp.auth import AuthConfig, auth_readme_section, auth_runtime_comment, render_auth_headers_function
from mule_to_mcp.model import Operation, Parameter

_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")

_TYPE_TO_PYTHON = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list[Any]",
    "object": "dict[str, Any]",
}

_TYPE_TO_JSONSCHEMA = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


# ---------------------------------------------------------------------------
# Parameter -> typed Python signature (adapted from openapi-to-mcp's
# schema_convert.py, operating on the shared Operation/Parameter model so it
# works identically whether the source spec was RAML or OpenAPI).
# ---------------------------------------------------------------------------


def sanitize_identifier(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    s = s.lower()
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "param"
    if s[0].isdigit():
        s = f"p_{s}"
    if keyword.iskeyword(s):
        s = f"{s}_"
    return s


def python_type_for_schema(schema: dict[str, Any], *, required: bool) -> str:
    oas_type = (schema or {}).get("type")
    base = _TYPE_TO_PYTHON.get(oas_type, "Any")
    if required or base == "Any":
        return base
    return f"{base} | None"


def json_schema_for_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    result: dict[str, Any] = {}
    t = schema.get("type")
    if t in _TYPE_TO_JSONSCHEMA:
        result["type"] = _TYPE_TO_JSONSCHEMA[t]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "description" in schema:
        result["description"] = schema["description"]
    if t == "array" and isinstance(schema.get("items"), dict):
        result["items"] = json_schema_for_schema(schema["items"])
    if t == "object" and isinstance(schema.get("properties"), dict):
        result["properties"] = {k: json_schema_for_schema(v) for k, v in schema["properties"].items()}
        if schema.get("required"):
            result["required"] = schema["required"]
    return result


@dataclass
class ToolParam:
    python_name: str
    original_name: str
    location: str  # "path" | "query" | "header" | "body"
    required: bool
    python_type: str
    json_schema: dict[str, Any]
    description: str = ""


def build_tool_params(operation: Operation) -> list[ToolParam]:
    """Required params first (declaration order), then optional ones."""
    used_names: set[str] = set()

    def unique(py_name: str) -> str:
        candidate = py_name
        i = 2
        while candidate in used_names:
            candidate = f"{py_name}_{i}"
            i += 1
        used_names.add(candidate)
        return candidate

    params: list[ToolParam] = []
    for p in operation.parameters:
        if p.location not in ("path", "query", "header"):
            continue
        py_name = unique(sanitize_identifier(p.name))
        required = p.required or p.location == "path"
        params.append(
            ToolParam(
                python_name=py_name,
                original_name=p.name,
                location=p.location,
                required=required,
                python_type=python_type_for_schema(p.schema, required=required),
                json_schema=json_schema_for_schema(p.schema),
                description=p.description,
            )
        )

    if operation.request_body is not None:
        rb = operation.request_body
        py_name = unique("body")
        params.append(
            ToolParam(
                python_name=py_name,
                original_name="body",
                location="body",
                required=rb.required,
                python_type=python_type_for_schema(rb.schema if rb.schema.get("type") else {"type": "object"}, required=rb.required),
                json_schema=json_schema_for_schema(rb.schema),
                description=rb.description or "Request body (JSON).",
            )
        )

    required_params = [p for p in params if p.required]
    optional_params = [p for p in params if not p.required]
    return required_params + optional_params


# ---------------------------------------------------------------------------
# Tool naming (identical approach to openapi-to-mcp: prefer operationId,
# else synthesize from method+path; RAML operations have no operationId so
# they always go through the synthesis path).
# ---------------------------------------------------------------------------


def _snake_segment(segment: str) -> str:
    segment = re.sub(r"[^0-9a-zA-Z]+", "_", segment)
    segment = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", segment)
    return segment.lower().strip("_")


def synthesize_tool_name(operation: Operation) -> str:
    parts = [operation.method.lower()]
    for raw_segment in operation.path.split("/"):
        if not raw_segment:
            continue
        m = _PATH_PARAM_RE.fullmatch(raw_segment)
        if m:
            parts.append(f"by_{_snake_segment(m.group(1))}")
        else:
            parts.append(_snake_segment(raw_segment))
    name = "_".join(p for p in parts if p)
    return name or f"{operation.method.lower()}_root"


def tool_name_for(operation: Operation, used_names: set[str]) -> str:
    if operation.operation_id:
        base = re.sub(r"[^0-9a-zA-Z_]", "_", operation.operation_id)
        base = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", base).lower().strip("_") or "tool"
    else:
        base = synthesize_tool_name(operation)
    if keyword.iskeyword(base):
        base = f"{base}_"
    name = base
    i = 2
    while name in used_names:
        name = f"{base}_{i}"
        i += 1
    used_names.add(name)
    return name


def _signature_for(params: list[ToolParam]) -> str:
    pieces = []
    for p in params:
        default = "" if p.required else " = None"
        pieces.append(f"{p.python_name}: {p.python_type}{default}")
    return ", ".join(pieces)


def _docstring_for(operation: Operation, params: list[ToolParam]) -> str:
    summary = (operation.summary or operation.description or "").strip()
    lines = [summary] if summary else ["(no description provided in the source spec)"]
    if params:
        lines.append("")
        lines.append("Args:")
        for p in params:
            desc = p.description.strip() or f"{p.location} parameter"
            req = "" if p.required else " (optional)"
            lines.append(f"    {p.python_name}: {desc}{req}")
    body = "\n    ".join(lines).replace('"""', "'''")
    return f'    """{body}\n    """'


def _call_expr_for(operation: Operation, params: list[ToolParam]) -> str:
    path_params = {p.original_name: p.python_name for p in params if p.location == "path"}
    query_params = {p.original_name: p.python_name for p in params if p.location == "query"}
    header_params = {p.original_name: p.python_name for p in params if p.location == "header"}
    body_param = next((p for p in params if p.location == "body"), None)

    def dict_literal(mapping: dict[str, str]) -> str:
        if not mapping:
            return "None"
        items = ", ".join(f"{k!r}: {v}" for k, v in mapping.items())
        return "{" + items + "}"

    args = [
        f'"{operation.method.upper()}"',
        f"{operation.path!r}",
        f"path_params={dict_literal(path_params)}",
        f"query_params={dict_literal(query_params)}",
        f"header_params={dict_literal(header_params)}",
        f"json_body={body_param.python_name if body_param else 'None'}",
    ]
    return "_call_api(" + ", ".join(args) + ")"


def _render_tool_function(operation: Operation, tool_name: str) -> str:
    params = build_tool_params(operation)
    signature = _signature_for(params)
    description = (operation.summary or operation.description or tool_name).strip().replace("\\", "\\\\").replace('"', '\\"')
    description = " ".join(description.splitlines())
    docstring = _docstring_for(operation, params)
    call_expr = _call_expr_for(operation, params)
    return (
        f'@mcp.tool(name="{tool_name}", description="{description}")\n'
        f"def {tool_name}({signature}) -> dict[str, Any]:\n"
        f"{docstring}\n"
        f"    return {call_expr}\n"
    )


_RUNTIME_HEADER = '''"""Generated MCP server — DO NOT EDIT BY HAND.

Generated by mule-to-mcp (https://github.com/) from the {source_format} spec:
  title: {title}
  source: {source}

Each function below is one MCP tool, one per (path, method) operation in the
source spec. Run it with:

    python server.py                  # stdio transport (for MCP hosts / clients)
    mcp dev server.py                 # MCP Inspector, for interactive testing

{auth_comment}
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests
from mcp.server import MCPServer

BASE_URL = {base_url!r}

mcp = MCPServer({server_name!r})


{auth_headers_function}

def _resolve_path(template: str, path_params: dict[str, Any] | None) -> str:
    resolved = template
    for name, value in (path_params or {{}}).items():
        resolved = resolved.replace("{{" + name + "}}", quote(str(value), safe=""))
    return resolved


def _call_api(
    method: str,
    path_template: str,
    *,
    path_params: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    header_params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> dict[str, Any]:
    """Perform the real HTTP call against BASE_URL for one generated tool."""
    path = _resolve_path(path_template, path_params)
    url = f"{{BASE_URL}}{{path}}"
    headers = _auth_headers()
    for k, v in (header_params or {{}}).items():
        if v is not None:
            headers[k] = str(v)
    query = {{k: v for k, v in (query_params or {{}}).items() if v is not None}}
    resp = requests.request(method, url, params=query, headers=headers, json=json_body, timeout=30)
    try:
        parsed_body: Any = resp.json()
    except ValueError:
        parsed_body = resp.text
    return {{"status_code": resp.status_code, "body": parsed_body}}


'''

_RUNTIME_FOOTER = """

if __name__ == "__main__":
    mcp.run()
"""


def render_server_py(
    operations: list[Operation],
    *,
    title: str,
    source: str,
    source_format: str,
    base_url: str,
    server_name: str,
    auth: AuthConfig,
) -> str:
    used_names: set[str] = set()
    tool_blocks = [_render_tool_function(op, tool_name_for(op, used_names)) for op in operations]

    header = _RUNTIME_HEADER.format(
        title=title,
        source=source,
        source_format=source_format.upper(),
        auth_comment=auth_runtime_comment(auth),
        base_url=base_url,
        server_name=server_name,
        auth_headers_function=render_auth_headers_function(auth),
    )
    return header + "\n\n".join(tool_blocks) + _RUNTIME_FOOTER


def render_pyproject_toml(project_name: str) -> str:
    return f'''[project]
name = "{project_name}"
version = "0.1.0"
description = "MCP server generated by mule-to-mcp"
requires-python = ">=3.10"
dependencies = [
    "mcp>=2.0,<3",
    "requests>=2.31",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = ["server"]
'''


def render_readme(
    *,
    title: str,
    source: str,
    source_format: str,
    base_url: str,
    auth: AuthConfig,
    tool_names: list[str],
) -> str:
    tools_list = "\n".join(f"- `{name}`" for name in tool_names) or "- (no operations found)"
    return f'''# {title} — generated MCP server

Generated by [mule-to-mcp](https://github.com/) from the {source_format.upper()} spec `{source}`.

Every tool below performs a real HTTP call to `{base_url}` — this is not a mock.

## Run it

```bash
pip install -e .
python server.py
```

Or, to poke at it interactively with the MCP Inspector:

```bash
mcp dev server.py
```

## Tools

{tools_list}

## Auth

{auth_readme_section(auth)}

## Regenerating

This file and `server.py` are generated. Re-run `mule-to-mcp generate` to
regenerate them from an updated spec rather than hand-editing.
'''


def generate_project(
    operations: list[Operation],
    *,
    title: str,
    source: str,
    source_format: str,
    base_url: str,
    out_dir: Path,
    auth: AuthConfig,
) -> dict[str, str]:
    """Render and write the full generated project to out_dir. Returns {filename: content}."""
    out_dir.mkdir(parents=True, exist_ok=True)

    server_name = re.sub(r"[^0-9a-zA-Z_-]+", "-", title).strip("-").lower() or "generated-mcp-server"
    project_name = f"{server_name}-mcp-server"

    used_names: set[str] = set()
    tool_names = [tool_name_for(op, used_names) for op in operations]

    files = {
        "server.py": render_server_py(
            operations,
            title=title,
            source=source,
            source_format=source_format,
            base_url=base_url,
            server_name=server_name,
            auth=auth,
        ),
        "pyproject.toml": render_pyproject_toml(project_name),
        "README.md": render_readme(
            title=title,
            source=source,
            source_format=source_format,
            base_url=base_url,
            auth=auth,
            tool_names=tool_names,
        ),
    }
    for filename, content in files.items():
        (out_dir / filename).write_text(content, encoding="utf-8")
    return files


__all__ = [
    "ToolParam",
    "build_tool_params",
    "generate_project",
    "render_pyproject_toml",
    "render_readme",
    "render_server_py",
    "synthesize_tool_name",
    "tool_name_for",
]
