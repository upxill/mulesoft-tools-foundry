"""Best-effort Anypoint Exchange asset fetch. **UNVERIFIED** -- see README
section "Exchange-pull mode (best-effort, unverified)" for full disclosure.

This environment has no Anypoint Platform account/credentials, so this code
path cannot be exercised against the real service. It is implemented against
the following researched-but-incomplete picture of the Exchange API (see
docstrings inline for exactly which facts came from where):

Confirmed via `https://docs.mulesoft.com/exchange/exchange-api` (fetched
2026-09-13) and web search of MuleSoft's public Exchange Experience API docs:
  - Assets are addressed by ``groupId``/``assetId``/``version``, and the v2
    Exchange API base path for an asset is
    ``https://anypoint.mulesoft.com/exchange/api/v2/organizations/{orgId}/assets/{groupId}/{assetId}/{version}``
    (the publish endpoint; docs did not show a public GET-for-download
    endpoint shape, so this is extrapolated by analogy -- see ASSUMPTIONS).
  - Authenticating with a connected app: HTTP Basic auth with username
    literal ``~~~Client~~~`` and password ``<clientId>~?~<clientSecret>``,
    OR a bearer token obtained by logging in, sent as
    ``Authorization: bearer <token>`` on every call.

NOT confirmed (no public doc page found in the time available) and therefore
ASSUMED, best-effort, for this implementation:
  1. A dedicated OAuth2 client_credentials token endpoint exists at
     ``https://anypoint.mulesoft.com/accounts/api/v2/oauth2/token`` accepting
     ``grant_type=client_credentials``, ``client_id``, ``client_secret`` as a
     JSON or form body, returning ``{"access_token": "..."}``. This mirrors
     the well-known shape of MuleSoft's identity/accounts API but was not
     directly confirmed against current public documentation in this session.
  2. A GET on the same v2 asset path returns asset metadata including a
     ``files`` array, each entry having a ``classifier`` (e.g. ``"raml"``,
     ``"oas"``, ``"fat-raml"``) and a ``externalLink`` or ``downloadURL`` to
     the actual spec file/archive.
  3. If the asset's main spec file is a RAML "fat archive" (a zip bundling
     the root RAML plus any `!include`d fragments), only a single
     self-contained RAML/OAS file at the archive root is handled -- zip
     extraction of a multi-file RAML project is NOT implemented (this
     generator's RAML parser only supports self-contained, single-file RAML
     per its own documented limitations).

If any of these assumptions is wrong, this module will raise a clear
``ExchangeError`` naming the HTTP status/response body it got back rather
than failing silently -- treat this feature as "shaped like it should work"
rather than "known to work."
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests

ANYPOINT_BASE_URL = "https://anypoint.mulesoft.com"
TOKEN_URL = f"{ANYPOINT_BASE_URL}/accounts/api/v2/oauth2/token"  # ASSUMED, see module docstring
ASSET_URL_TEMPLATE = f"{ANYPOINT_BASE_URL}/exchange/api/v2/assets/{{group_id}}/{{asset_id}}/{{version}}"


class ExchangeError(Exception):
    """Raised when the (unverified, best-effort) Exchange pull fails."""


@dataclass
class ExchangeAssetRef:
    group_id: str
    asset_id: str
    version: str

    @classmethod
    def parse(cls, ref: str) -> "ExchangeAssetRef":
        """Parse the `--exchange-asset org/assetId/version` CLI value."""
        parts = ref.split("/")
        if len(parts) != 3 or not all(parts):
            raise ExchangeError(
                f"Invalid --exchange-asset value {ref!r}. Expected 'groupId/assetId/version', "
                "e.g. '68ef9520-24e9-4cf2-b498-2f0e9e6d3d13/orders-api/1.0.0'."
            )
        group_id, asset_id, version = parts
        return cls(group_id=group_id, asset_id=asset_id, version=version)


def _get_bearer_token(client_id: str, client_secret: str, *, timeout: int = 30) -> str:
    """POST client_id/client_secret for a bearer token. ASSUMED endpoint shape -- unverified."""
    try:
        resp = requests.post(
            TOKEN_URL,
            json={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ExchangeError(f"Could not reach Anypoint token endpoint {TOKEN_URL}: {exc}") from exc
    if resp.status_code != 200:
        raise ExchangeError(
            f"Anypoint token request failed: HTTP {resp.status_code} — {resp.text[:500]}\n"
            "This endpoint shape is unverified (see exchange_client.py docstring); if MuleSoft's real "
            "token endpoint differs, this is the first place to fix."
        )
    try:
        token = resp.json()["access_token"]
    except (ValueError, KeyError) as exc:
        raise ExchangeError(f"Unexpected token response shape: {resp.text[:500]}") from exc
    return str(token)


def fetch_asset_spec_text(asset_ref: ExchangeAssetRef, client_id: str, client_secret: str, *, timeout: int = 30) -> tuple[str, str]:
    """Fetch an asset's main spec file text from Anypoint Exchange.

    Returns (spec_text, source_description). Raises ExchangeError on any
    failure, with the underlying HTTP status/body included so a user hitting
    a real Anypoint org can tell exactly where the assumed API shape broke.
    """
    token = _get_bearer_token(client_id, client_secret, timeout=timeout)
    asset_url = ASSET_URL_TEMPLATE.format(group_id=asset_ref.group_id, asset_id=asset_ref.asset_id, version=asset_ref.version)
    headers = {"Authorization": f"bearer {token}"}

    try:
        resp = requests.get(asset_url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ExchangeError(f"Could not reach Exchange asset endpoint {asset_url}: {exc}") from exc
    if resp.status_code != 200:
        raise ExchangeError(
            f"Exchange asset lookup failed: HTTP {resp.status_code} — {resp.text[:500]}\n"
            f"URL: {asset_url}\n"
            "This endpoint shape is unverified (no Anypoint credentials were available to test it "
            "live) -- see the README's 'Exchange-pull mode' section and exchange_client.py's "
            "module docstring for exactly what was assumed."
        )

    try:
        metadata = resp.json()
    except ValueError as exc:
        raise ExchangeError(f"Exchange asset response was not JSON: {resp.text[:500]}") from exc

    files = metadata.get("files") or []
    spec_file = next(
        (f for f in files if str(f.get("classifier", "")).lower() in ("raml", "oas", "fat-raml", "fat-oas")),
        None,
    )
    if spec_file is None:
        raise ExchangeError(
            f"No RAML/OAS file found in Exchange asset metadata for {asset_ref.group_id}/{asset_ref.asset_id}/{asset_ref.version}. "
            f"Got file classifiers: {[f.get('classifier') for f in files]!r}. "
            "This assumes 'files[].classifier' and 'files[].externalLink' exist on the response "
            "(unverified -- see module docstring)."
        )

    download_url = spec_file.get("externalLink") or spec_file.get("downloadURL")
    if not download_url:
        raise ExchangeError(f"Spec file entry had no downloadable URL: {json.dumps(spec_file)[:500]}")

    try:
        file_resp = requests.get(download_url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ExchangeError(f"Could not download spec file from {download_url}: {exc}") from exc
    if file_resp.status_code != 200:
        raise ExchangeError(f"Downloading spec file failed: HTTP {file_resp.status_code} from {download_url}")

    source = f"exchange:{asset_ref.group_id}/{asset_ref.asset_id}/{asset_ref.version} ({spec_file.get('classifier')})"
    return file_resp.text, source


__all__ = ["ExchangeAssetRef", "ExchangeError", "fetch_asset_spec_text"]
