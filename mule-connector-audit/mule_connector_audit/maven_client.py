"""A thin client for the real, public Maven Central Search API.

Endpoint shape, verified live against `search.maven.org` during development
(see README "Live verification" section for the actual captured
responses):

    GET https://search.maven.org/solrsearch/select
        ?q=g:"{groupId}"+AND+a:"{artifactId}"
        &core=gav
        &rows=100
        &wt=json

`core=gav` returns one document *per published version* (fields `g`, `a`,
`v`, `timestamp`), which lets this client determine the true latest version
itself using `versioning.py`'s comparator, rather than trusting Maven
Central's own "latestVersion" convenience field (which was cross-checked
during development and agreed with this approach for every coordinate
tried, but relying on our own comparator keeps behavior consistent and
testable). `timestamp` is epoch milliseconds of when that version's POM
was indexed, used as the release date.

No third-party HTTP library is required -- this uses only `urllib` from the
standard library, so `pip install mule-connector-audit` (or just running
this repo out of a plain venv) pulls in zero runtime dependencies for the
entire core audit path.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from .versioning import parse_version

SEARCH_URL = "https://search.maven.org/solrsearch/select"
DEFAULT_TIMEOUT = 10.0
DEFAULT_USER_AGENT = "mule-connector-audit/0.1 (+https://github.com/)"


class MavenNetworkError(Exception):
    """A real network-layer failure talking to Maven Central.

    Distinct from "this coordinate simply isn't published there" (which is
    a normal, successful HTTP 200 response with zero results) -- this is
    for timeouts, DNS failures, connection refused, TLS errors, non-2xx
    HTTP responses, and unparsable responses. Callers should show a clean
    message and exit non-zero rather than let this propagate as a raw
    traceback.
    """


@dataclass
class MavenLookupResult:
    group_id: str
    artifact_id: str
    found: bool
    latest_version: str | None = None
    release_timestamp_ms: int | None = None
    version_count: int = 0

    @property
    def release_date(self) -> datetime | None:
        if self.release_timestamp_ms is None:
            return None
        return datetime.fromtimestamp(self.release_timestamp_ms / 1000.0, tz=timezone.utc)


class MavenCentralClient:
    """Queries the real Maven Central Search API (search.maven.org)."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, rows: int = 100):
        self.timeout = timeout
        self.rows = rows

    def get_latest_version(self, group_id: str, artifact_id: str) -> MavenLookupResult:
        query = f'g:"{group_id}" AND a:"{artifact_id}"'
        params = {
            "q": query,
            "core": "gav",
            "rows": str(self.rows),
            "wt": "json",
        }
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        data = self._get_json(url)

        try:
            docs = data["response"]["docs"]
        except (KeyError, TypeError) as exc:
            raise MavenNetworkError(
                f"Unexpected response shape from Maven Central for "
                f"{group_id}:{artifact_id} (missing response.docs): {exc}"
            ) from exc

        if not docs:
            return MavenLookupResult(
                group_id=group_id, artifact_id=artifact_id, found=False
            )

        best_doc = None
        best_key = None
        for doc in docs:
            v = doc.get("v")
            if v is None:
                continue
            key = parse_version(v).rank_tuple
            if best_key is None or key > best_key:
                best_key = key
                best_doc = doc

        if best_doc is None:
            return MavenLookupResult(
                group_id=group_id, artifact_id=artifact_id, found=False
            )

        return MavenLookupResult(
            group_id=group_id,
            artifact_id=artifact_id,
            found=True,
            latest_version=best_doc.get("v"),
            release_timestamp_ms=best_doc.get("timestamp"),
            version_count=len(docs),
        )

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise MavenNetworkError(
                        f"Maven Central Search API returned HTTP {status} for {url}"
                    )
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise MavenNetworkError(
                    "Maven Central Search API rate-limited this request (HTTP 429). "
                    "Try again shortly, or reduce how often you run the audit."
                ) from exc
            raise MavenNetworkError(
                f"Maven Central Search API returned HTTP {exc.code} {exc.reason} for {url}"
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
                raise MavenNetworkError(
                    f"Timed out connecting to Maven Central Search API "
                    f"(search.maven.org) after {self.timeout}s. Check your network "
                    f"connection, or increase --timeout."
                ) from exc
            raise MavenNetworkError(
                f"Could not reach Maven Central Search API (search.maven.org): {reason}. "
                f"Check your network connection / DNS / proxy settings."
            ) from exc
        except socket.timeout as exc:
            raise MavenNetworkError(
                f"Timed out connecting to Maven Central Search API "
                f"(search.maven.org) after {self.timeout}s."
            ) from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise MavenNetworkError(
                f"Maven Central Search API returned a response that wasn't valid JSON: {exc}"
            ) from exc
