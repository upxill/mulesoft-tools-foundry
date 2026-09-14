"""Command-line entry point:

    mule-to-mcp generate SPEC --out DIR [--auth-mode auto|client-id|oauth2|none]
        [--client-id-header H] [--client-secret-header H]
        [--client-id-env VAR] [--client-secret-env VAR] [--bearer-token-env VAR]

    mule-to-mcp generate --exchange-asset GROUP/ASSET/VERSION --out DIR [...]
        (best-effort, unverified -- see README)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mule_to_mcp import oas_parser, raml_parser
from mule_to_mcp.auth import VALID_MODES, AuthConfig, resolve_auth_mode
from mule_to_mcp.codegen import generate_project, tool_name_for
from mule_to_mcp.exchange_client import ExchangeAssetRef, ExchangeError, fetch_asset_spec_text
from mule_to_mcp.model import DetectedSecurity, Operation


class GenerateError(Exception):
    pass


def _load_and_parse(raw_text: str, source: str) -> tuple[str, str, str, list[Operation], DetectedSecurity]:
    """Auto-detect RAML vs OAS from content, parse, and return the shared shape:
    (format, title, base_url, operations, detected_security).
    """
    if raml_parser.looks_like_raml(raw_text):
        doc = raml_parser.load_raml(raw_text)
        return (
            "raml",
            raml_parser.get_api_title(doc),
            raml_parser.get_base_url(doc),
            raml_parser.extract_operations(doc),
            raml_parser.detect_security(doc),
        )
    if oas_parser.looks_like_oas(raw_text):
        spec = oas_parser.load_spec(source, raw_text=raw_text)
        return (
            "oas",
            oas_parser.get_api_title(spec),
            oas_parser.get_base_url(spec),
            oas_parser.extract_operations(spec),
            oas_parser.detect_security(spec),
        )
    raise GenerateError(
        f"Could not detect spec format for {source!r}: it starts neither with '#%RAML 1.0' "
        "nor with an 'openapi:'/'swagger:' key. Only RAML 1.0 and OpenAPI 3.x are supported."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule-to-mcp",
        description="Turn a RAML 1.0 or OpenAPI spec into a runnable MCP server, with MuleSoft-flavored auth wiring.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate an MCP server project from a RAML/OAS spec")
    generate.add_argument(
        "spec",
        nargs="?",
        default=None,
        help="Path or URL to a RAML 1.0 or OpenAPI 3.x document. Omit if using --exchange-asset.",
    )
    generate.add_argument("--out", required=True, help="Output directory for the generated MCP server project")
    generate.add_argument(
        "--exchange-asset",
        default=None,
        metavar="GROUP/ASSET/VERSION",
        help=(
            "Fetch the spec from Anypoint Exchange instead of a local file/URL "
            "(best-effort, unverified -- see README). Requires ANYPOINT_CLIENT_ID / "
            "ANYPOINT_CLIENT_SECRET in the environment."
        ),
    )
    generate.add_argument(
        "--auth-mode",
        choices=("auto", *VALID_MODES),
        default="auto",
        help=(
            "'auto' (default) detects Client ID enforcement / OAuth2 from the spec's securitySchemes; "
            "or force one explicitly: 'client-id', 'oauth2', or 'none'."
        ),
    )
    generate.add_argument(
        "--client-id-header",
        default=None,
        metavar="HEADER",
        help="Header name for the client id in --auth-mode client-id (default: detected, else 'client_id').",
    )
    generate.add_argument(
        "--client-secret-header",
        default=None,
        metavar="HEADER",
        help="Header name for the client secret in --auth-mode client-id (default: detected, else 'client_secret').",
    )
    generate.add_argument(
        "--client-id-env",
        default=None,
        metavar="VAR_NAME",
        help="Env var the GENERATED server reads for the client id (default: ANYPOINT_CLIENT_ID). Not read at generation time.",
    )
    generate.add_argument(
        "--client-secret-env",
        default=None,
        metavar="VAR_NAME",
        help="Env var the GENERATED server reads for the client secret (default: ANYPOINT_CLIENT_SECRET). Not read at generation time.",
    )
    generate.add_argument(
        "--bearer-token-env",
        default=None,
        metavar="VAR_NAME",
        help="Env var the GENERATED server reads for an OAuth2 bearer token in --auth-mode oauth2 (default: MULE_BEARER_TOKEN).",
    )
    return parser


def _build_auth_config(args: argparse.Namespace, detected: DetectedSecurity) -> AuthConfig:
    mode = resolve_auth_mode(args.auth_mode, detected)
    kwargs: dict[str, str] = {}
    if args.client_id_header:
        kwargs["client_id_header"] = args.client_id_header
    elif detected.client_id_header:
        kwargs["client_id_header"] = detected.client_id_header
    if args.client_secret_header:
        kwargs["client_secret_header"] = args.client_secret_header
    elif detected.client_secret_header:
        kwargs["client_secret_header"] = detected.client_secret_header
    if args.client_id_env:
        kwargs["client_id_env"] = args.client_id_env
    if args.client_secret_env:
        kwargs["client_secret_env"] = args.client_secret_env
    if args.bearer_token_env:
        kwargs["bearer_token_env"] = args.bearer_token_env
    return AuthConfig(mode=mode, **kwargs)


def run_generate(args: argparse.Namespace) -> int:
    if bool(args.spec) == bool(args.exchange_asset):
        print("error: pass exactly one of SPEC (path/URL) or --exchange-asset GROUP/ASSET/VERSION", file=sys.stderr)
        return 1

    try:
        if args.exchange_asset:
            import os

            client_id = os.environ.get("ANYPOINT_CLIENT_ID")
            client_secret = os.environ.get("ANYPOINT_CLIENT_SECRET")
            if not client_id or not client_secret:
                print(
                    "error: --exchange-asset requires ANYPOINT_CLIENT_ID and ANYPOINT_CLIENT_SECRET "
                    "environment variables to be set.",
                    file=sys.stderr,
                )
                return 1
            print(
                "note: Exchange-pull mode is best-effort/unverified (no Anypoint credentials exist in this "
                "environment to test it live). See README 'Exchange-pull mode' section.",
            )
            asset_ref = ExchangeAssetRef.parse(args.exchange_asset)
            try:
                raw_text, source = fetch_asset_spec_text(asset_ref, client_id, client_secret)
            except ExchangeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        else:
            source = args.spec
            try:
                raw_text = oas_parser.fetch_text(args.spec)
            except oas_parser.OASParseError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

        try:
            source_format, title, base_url, operations, detected = _load_and_parse(raw_text, source)
        except (raml_parser.RamlParseError, oas_parser.OASParseError, GenerateError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if not operations:
            print("error: no operations found in spec (empty or missing resources/paths)", file=sys.stderr)
            return 1

        auth = _build_auth_config(args, detected)
        out_dir = Path(args.out)

        generate_project(
            operations,
            title=title,
            source=source,
            source_format=source_format,
            base_url=base_url,
            out_dir=out_dir,
            auth=auth,
        )

        used_names: set[str] = set()
        tool_names = [tool_name_for(op, used_names) for op in operations]

        print(f"Detected format: {source_format.upper()}")
        print(f"Detected security: {detected.kind} — {detected.detail}")
        print(f"Generated MCP server for '{title}' -> {out_dir}/")
        print(f"  base URL: {base_url}")
        print(f"  {len(operations)} operation(s) -> {len(tool_names)} tool(s):")
        for name in tool_names:
            print(f"    - {name}")
        if auth.mode == "client-id":
            print(
                f"  auth: Client ID enforcement — headers '{auth.client_id_header}'/'{auth.client_secret_header}' "
                f"from ${auth.client_id_env}/${auth.client_secret_env} (set at runtime, not stored)"
            )
        elif auth.mode == "oauth2":
            print(f"  auth: OAuth2 bearer token from ${auth.bearer_token_env} (set at runtime, not stored)")
        else:
            print("  auth: none")
        print()
        print("Next steps:")
        print(f"  cd {out_dir}")
        print("  pip install -e .")
        print("  python server.py")
        return 0
    except Exception as exc:  # pragma: no cover - safety net for unexpected failures
        print(f"error: unexpected failure: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        return run_generate(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
