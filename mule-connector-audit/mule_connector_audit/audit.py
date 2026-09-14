"""Ties pom_parser + maven_client + versioning together into an audit run.

Zero LLM involvement anywhere in this module -- everything here is
deterministic parsing, a real HTTP call to a public API, and comparison
logic. See `explain.py` for the entirely-optional, off-by-default LLM
summary layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .maven_client import MavenCentralClient, MavenLookupResult, MavenNetworkError
from .pom_parser import Dependency, parse_pom
from .versioning import Staleness, classify

DEFAULT_ABANDONED_DAYS = 730  # ~2 years


@dataclass
class ConnectorAuditEntry:
    dependency: Dependency
    lookup: MavenLookupResult | None
    staleness: Staleness
    possibly_abandoned: bool
    days_since_release: int | None
    detail: str

    @property
    def group_id(self) -> str:
        return self.dependency.group_id

    @property
    def artifact_id(self) -> str:
        return self.dependency.artifact_id

    @property
    def pinned_version(self) -> str | None:
        return self.dependency.version

    @property
    def latest_version(self) -> str | None:
        return self.lookup.latest_version if self.lookup else None

    @property
    def release_date_str(self) -> str:
        if self.lookup and self.lookup.release_date:
            return self.lookup.release_date.strftime("%Y-%m-%d")
        return "n/a"


@dataclass
class AuditReport:
    pom_path: Path
    total_dependencies: int
    entries: list[ConnectorAuditEntry]

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Staleness}
        counts["possibly-abandoned"] = 0
        for e in self.entries:
            counts[e.staleness.value] += 1
            if e.possibly_abandoned:
                counts["possibly-abandoned"] += 1
        return counts


def run_audit(
    pom_path: str | Path,
    client: MavenCentralClient | None = None,
    abandoned_days: int = DEFAULT_ABANDONED_DAYS,
    now: datetime | None = None,
) -> AuditReport:
    """Run a full audit of a pom.xml's MuleSoft connector dependencies.

    Raises `mule_connector_audit.pom_parser.PomParseError` if the pom.xml
    itself can't be parsed, and `mule_connector_audit.maven_client.MavenNetworkError`
    if a real network failure occurs talking to Maven Central. Both are
    meant to be caught by the CLI layer and shown as clean error messages.
    """
    parsed = parse_pom(pom_path)
    client = client or MavenCentralClient()
    now = now or datetime.now(timezone.utc)

    entries: list[ConnectorAuditEntry] = []
    for dep in parsed.connectors:
        entries.append(_audit_one(dep, client, abandoned_days, now))

    return AuditReport(
        pom_path=Path(pom_path),
        total_dependencies=len(parsed.dependencies),
        entries=entries,
    )


def _audit_one(
    dep: Dependency,
    client: MavenCentralClient,
    abandoned_days: int,
    now: datetime,
) -> ConnectorAuditEntry:
    lookup = client.get_latest_version(dep.group_id, dep.artifact_id)

    if not lookup.found:
        return ConnectorAuditEntry(
            dependency=dep,
            lookup=lookup,
            staleness=Staleness.NOT_FOUND,
            possibly_abandoned=False,
            days_since_release=None,
            detail=(
                "Not found on Maven Central. This is common for premium/certified "
                "connectors (e.g. under com.mulesoft.connectors) distributed only "
                "via Anypoint Exchange / MuleSoft's own repository -- it does not "
                "necessarily mean the connector itself is abandoned."
            ),
        )

    pinned = dep.version
    if pinned is None:
        return ConnectorAuditEntry(
            dependency=dep,
            lookup=lookup,
            staleness=Staleness.UNPINNED,
            possibly_abandoned=False,
            days_since_release=None,
            detail=(
                "No pinned <version> could be determined (either omitted, or a "
                "${property} reference that could not be resolved from <properties>). "
                f"Latest known on Maven Central is {lookup.latest_version}."
            ),
        )

    staleness = classify(pinned, lookup.latest_version)

    days_since_release = None
    possibly_abandoned = False
    if lookup.release_date is not None:
        days_since_release = (now - lookup.release_date).days
        possibly_abandoned = days_since_release >= abandoned_days

    detail_parts = [
        f"Pinned {pinned} vs. latest {lookup.latest_version} on Maven Central "
        f"({lookup.version_count} version(s) indexed)."
    ]
    if days_since_release is not None:
        years = days_since_release / 365.25
        detail_parts.append(
            f"Latest version was released {days_since_release} days ago "
            f"(~{years:.1f} years)."
        )
    if possibly_abandoned:
        detail_parts.append(
            f"Flagged possibly abandoned: no newer version indexed on Maven "
            f"Central in over {abandoned_days} days."
        )

    return ConnectorAuditEntry(
        dependency=dep,
        lookup=lookup,
        staleness=staleness,
        possibly_abandoned=possibly_abandoned,
        days_since_release=days_since_release,
        detail=" ".join(detail_parts),
    )
