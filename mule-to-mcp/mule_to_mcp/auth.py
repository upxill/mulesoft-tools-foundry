"""MuleSoft-flavored auth: Client ID enforcement (two headers) and OAuth 2.0
(bearer token) header-injection code generation.

This is the extension of openapi-to-mcp's single `--auth-env` bearer-token
pass-through pattern to the two-header "Client ID enforcement" shape that is
extremely common on Anypoint-managed APIs: every request must carry a
connected app's client id and client secret as two HTTP headers (commonly
named `client_id`/`client_secret`, or on Anypoint's own managed APIs
`X-ANYPOINT-CLIENT-ID`/`X-ANYPOINT-CLIENT-SECRET`). Real-world Mule API
gateways name these headers differently per organization, so the exact header
names are always a CLI flag here (`--client-id-header`/`--client-secret-header`),
never hardcoded.

Three supported modes, selected by `--auth-mode`:
    "none"       -- no auth headers are sent.
    "client-id"  -- two headers, read from two env vars, at call time.
    "oauth2"     -- `Authorization: Bearer <token>`, token read from one env
                    var at call time. This is header pass-through only: no
                    token acquisition, refresh, or OAuth flow is implemented
                    (see README limitations) -- you are expected to obtain a
                    valid access token yourself and export it.
"""

from __future__ import annotations

from dataclasses import dataclass

from mule_to_mcp.model import DetectedSecurity

VALID_MODES = ("none", "client-id", "oauth2")

DEFAULT_CLIENT_ID_HEADER = "client_id"
DEFAULT_CLIENT_SECRET_HEADER = "client_secret"
DEFAULT_CLIENT_ID_ENV = "ANYPOINT_CLIENT_ID"
DEFAULT_CLIENT_SECRET_ENV = "ANYPOINT_CLIENT_SECRET"
DEFAULT_BEARER_TOKEN_ENV = "MULE_BEARER_TOKEN"


@dataclass
class AuthConfig:
    mode: str  # "none" | "client-id" | "oauth2"
    client_id_header: str = DEFAULT_CLIENT_ID_HEADER
    client_secret_header: str = DEFAULT_CLIENT_SECRET_HEADER
    client_id_env: str = DEFAULT_CLIENT_ID_ENV
    client_secret_env: str = DEFAULT_CLIENT_SECRET_ENV
    bearer_token_env: str = DEFAULT_BEARER_TOKEN_ENV

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"Invalid auth mode {self.mode!r}; must be one of {VALID_MODES}")


def resolve_auth_mode(requested_mode: str, detected: DetectedSecurity) -> str:
    """Resolve 'auto' against a DetectedSecurity guess; pass through explicit modes unchanged."""
    if requested_mode != "auto":
        return requested_mode
    if detected.kind in ("client-id", "oauth2"):
        return detected.kind
    return "none"


def render_auth_headers_function(auth: AuthConfig) -> str:
    """Render the body of the generated server's `_auth_headers()` function."""
    if auth.mode == "client-id":
        return (
            "def _auth_headers() -> dict[str, str]:\n"
            '    """Read the Anypoint Client ID enforcement credentials from the environment\n'
            "    at call time (never hardcoded, never logged) and send them as the two\n"
            "    headers the API's Client ID enforcement policy expects.\n"
            '    """\n'
            f"    client_id = os.environ.get({auth.client_id_env!r})\n"
            f"    client_secret = os.environ.get({auth.client_secret_env!r})\n"
            "    headers: dict[str, str] = {}\n"
            "    if client_id:\n"
            f"        headers[{auth.client_id_header!r}] = client_id\n"
            "    if client_secret:\n"
            f"        headers[{auth.client_secret_header!r}] = client_secret\n"
            "    return headers\n"
        )
    if auth.mode == "oauth2":
        return (
            "def _auth_headers() -> dict[str, str]:\n"
            '    """Read the OAuth2 bearer token from the environment at call time (never\n'
            "    hardcoded, never logged). This is pass-through only: mule-to-mcp does not\n"
            "    perform the OAuth2 flow or refresh the token -- obtain a valid access token\n"
            "    yourself (e.g. via Anypoint's client_credentials token endpoint) and export it.\n"
            '    """\n'
            f"    token = os.environ.get({auth.bearer_token_env!r})\n"
            "    if not token:\n"
            "        return {}\n"
            '    return {"Authorization": f"Bearer {token}"}\n'
        )
    return (
        "def _auth_headers() -> dict[str, str]:\n"
        '    """No auth configured (generated with --auth-mode none)."""\n'
        "    return {}\n"
    )


def auth_runtime_comment(auth: AuthConfig) -> str:
    """Human-readable comment describing the auth wiring, for the generated server's docstring."""
    if auth.mode == "client-id":
        return (
            "Auth: Client ID enforcement. Every call sends two headers,\n"
            f"  {auth.client_id_header!r}: ${auth.client_id_env}\n"
            f"  {auth.client_secret_header!r}: ${auth.client_secret_env}\n"
            "read from the environment at call time. Export both before starting this server:\n"
            f"    export {auth.client_id_env}=your-client-id\n"
            f"    export {auth.client_secret_env}=your-client-secret"
        )
    if auth.mode == "oauth2":
        return (
            f"Auth: OAuth 2.0 bearer token pass-through. Every call sends\n"
            f'  Authorization: Bearer ${auth.bearer_token_env}\n'
            "read from the environment at call time (no token acquisition/refresh is\n"
            "performed by this generated server -- obtain the token yourself). Export it:\n"
            f"    export {auth.bearer_token_env}=your-access-token"
        )
    return "Auth: none configured (re-run `mule-to-mcp generate --auth-mode client-id|oauth2 ...` to add auth headers)."


def auth_readme_section(auth: AuthConfig) -> str:
    if auth.mode == "client-id":
        return (
            f"This server was generated with `--auth-mode client-id` "
            f"(headers `{auth.client_id_header}` / `{auth.client_secret_header}`). "
            f"Set both environment variables before starting the server:\n\n"
            f"```bash\n"
            f"export {auth.client_id_env}=your-client-id\n"
            f"export {auth.client_secret_env}=your-client-secret\n"
            f"python server.py\n"
            f"```\n\n"
            f"Every request then carries `{auth.client_id_header}: <id>` and "
            f"`{auth.client_secret_header}: <secret>`."
        )
    if auth.mode == "oauth2":
        return (
            f"This server was generated with `--auth-mode oauth2`. Set `{auth.bearer_token_env}` to a "
            f"valid OAuth2 access token before starting the server:\n\n"
            f"```bash\n"
            f"export {auth.bearer_token_env}=your-access-token\n"
            f"python server.py\n"
            f"```\n\n"
            f"Every request then carries `Authorization: Bearer ${auth.bearer_token_env}`. "
            f"**This is pass-through only** -- no token acquisition or refresh is performed; "
            f"you are responsible for obtaining a valid, unexpired token."
        )
    return (
        "This server was generated with `--auth-mode none`, so no auth headers are sent. "
        "Re-run `mule-to-mcp generate ... --auth-mode client-id` or `--auth-mode oauth2` to add auth wiring."
    )


__all__ = [
    "DEFAULT_BEARER_TOKEN_ENV",
    "DEFAULT_CLIENT_ID_ENV",
    "DEFAULT_CLIENT_ID_HEADER",
    "DEFAULT_CLIENT_SECRET_ENV",
    "DEFAULT_CLIENT_SECRET_HEADER",
    "VALID_MODES",
    "AuthConfig",
    "auth_readme_section",
    "auth_runtime_comment",
    "render_auth_headers_function",
    "resolve_auth_mode",
]
