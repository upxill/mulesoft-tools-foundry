"""A fake Maven Central client for offline tests -- no network access."""

from __future__ import annotations

from mule_connector_audit.maven_client import MavenLookupResult


class FakeMavenClient:
    """Stubbed drop-in for MavenCentralClient.get_latest_version.

    `catalog` maps "groupId:artifactId" -> (latest_version, timestamp_ms).
    Coordinates not present in the catalog report as not-found, exactly
    like a real 200-with-zero-docs Maven Central response.
    """

    def __init__(self, catalog: dict[str, tuple[str, int]]):
        self.catalog = catalog
        self.calls: list[tuple[str, str]] = []

    def get_latest_version(self, group_id: str, artifact_id: str) -> MavenLookupResult:
        self.calls.append((group_id, artifact_id))
        key = f"{group_id}:{artifact_id}"
        if key not in self.catalog:
            return MavenLookupResult(group_id=group_id, artifact_id=artifact_id, found=False)
        latest_version, timestamp_ms = self.catalog[key]
        return MavenLookupResult(
            group_id=group_id,
            artifact_id=artifact_id,
            found=True,
            latest_version=latest_version,
            release_timestamp_ms=timestamp_ms,
            version_count=1,
        )
