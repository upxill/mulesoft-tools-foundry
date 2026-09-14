"""Deterministic Maven pom.xml parsing for Mule 4 projects.

No network access, no LLM involvement -- pure `xml.etree.ElementTree` parsing.
Handles the `http://maven.apache.org/POM/4.0.0` namespace that every real
Maven pom.xml declares (and the (rare, invalid-but-seen-in-the-wild)
namespace-less pom.xml some hand-edited projects have), and resolves simple
`${property.name}` version placeholders against the pom's own `<properties>`
block, since real-world Mule projects very commonly pin connector versions
that way (e.g. `<http.connector.version>1.7.2</http.connector.version>` +
`<version>${http.connector.version}</version>`).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MAVEN_NS = "http://maven.apache.org/POM/4.0.0"

# groupId prefixes that identify a MuleSoft connector dependency. `startswith`
# is used (not exact match) so that any sub-group under these vendors is
# still picked up.
CONNECTOR_GROUP_PREFIXES = (
    "org.mule.connectors",
    "com.mulesoft.connectors",
)

_PROPERTY_REF_RE = re.compile(r"^\$\{\s*([^}\s]+)\s*\}$")


@dataclass
class Dependency:
    """One <dependency> entry from a pom.xml."""

    group_id: str
    artifact_id: str
    raw_version: str | None  # exactly what was in <version>, before resolution
    resolved_version: str | None  # after resolving ${property} references, if possible
    scope: str | None = None
    classifier: str | None = None

    @property
    def version(self) -> str | None:
        """The best-known, USABLE version string for comparison purposes.

        This is the resolved version when resolution succeeded (including
        the common case of a plain literal version, which "resolves" to
        itself). If <version> was a `${property}` reference that could not
        be resolved against <properties>, this is None -- callers must not
        treat the literal, unresolved `${...}` placeholder text as if it
        were a real version string (see `version_was_unresolved`).
        """
        if self.version_was_unresolved:
            return None
        return self.resolved_version if self.resolved_version is not None else self.raw_version

    @property
    def coordinates(self) -> str:
        return f"{self.group_id}:{self.artifact_id}"

    @property
    def is_connector(self) -> bool:
        return any(self.group_id.startswith(p) for p in CONNECTOR_GROUP_PREFIXES)

    @property
    def version_was_unresolved(self) -> bool:
        """True if <version> referenced a ${property} we could not resolve."""
        return (
            self.raw_version is not None
            and _PROPERTY_REF_RE.match(self.raw_version.strip()) is not None
            and self.resolved_version is None
        )


@dataclass
class ParsedPom:
    path: Path
    properties: dict[str, str] = field(default_factory=dict)
    dependencies: list[Dependency] = field(default_factory=list)

    @property
    def connectors(self) -> list[Dependency]:
        return [d for d in self.dependencies if d.is_connector]


class PomParseError(Exception):
    """Raised when a pom.xml cannot be parsed at all (malformed XML, missing file)."""


def _local_name(tag: str) -> str:
    """Strip a `{namespace}` prefix off an ElementTree tag, if present."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _find_children_by_local_name(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in list(elem) if _local_name(c.tag) == name]


def _find_child_by_local_name(elem: ET.Element, name: str) -> ET.Element | None:
    matches = _find_children_by_local_name(elem, name)
    return matches[0] if matches else None


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    t = elem.text.strip()
    return t if t else None


def _extract_properties(root: ET.Element) -> dict[str, str]:
    props: dict[str, str] = {}
    props_elem = _find_child_by_local_name(root, "properties")
    if props_elem is None:
        return props
    for child in list(props_elem):
        name = _local_name(child.tag)
        value = child.text.strip() if child.text else ""
        props[name] = value
    return props


# A handful of standard Maven built-in properties that commonly appear in
# version placeholders even though they are not declared under <properties>.
def _builtin_properties(root: ET.Element) -> dict[str, str]:
    builtins: dict[str, str] = {}
    version_elem = _find_child_by_local_name(root, "version")
    if version_elem is not None and version_elem.text:
        builtins["project.version"] = version_elem.text.strip()
    return builtins


def _resolve_property_ref(raw: str | None, properties: dict[str, str]) -> str | None:
    """Resolve a `${prop.name}` reference against `properties`.

    Only handles the common case of the *entire* version string being a
    single property reference (that's how real Mule pom.xml files pin
    connector versions). A literal, non-`${...}` version string is returned
    unchanged. Returns None if it's a property reference that isn't
    declared anywhere -- callers can detect this via
    `Dependency.version_was_unresolved`.
    """
    if raw is None:
        return raw
    m = _PROPERTY_REF_RE.match(raw.strip())
    if not m:
        return raw
    prop_name = m.group(1)
    resolved = properties.get(prop_name)
    if resolved is None:
        return None
    # A property can itself reference another property one level deep.
    m2 = _PROPERTY_REF_RE.match(resolved.strip())
    if m2:
        resolved = properties.get(m2.group(1), resolved)
    return resolved


def parse_pom(path: str | Path) -> ParsedPom:
    """Parse a pom.xml file into a ParsedPom.

    Raises PomParseError (never a raw ElementTree/OSError traceback) if the
    file is missing or not well-formed XML.
    """
    path = Path(path)
    if not path.exists():
        raise PomParseError(f"pom.xml not found at: {path}")
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise PomParseError(f"'{path}' is not well-formed XML: {exc}") from exc
    except OSError as exc:
        raise PomParseError(f"could not read '{path}': {exc}") from exc

    root = tree.getroot()
    root_local = _local_name(root.tag)
    if root_local != "project":
        raise PomParseError(
            f"'{path}' does not look like a Maven pom.xml (root element is "
            f"<{root_local}>, expected <project>)"
        )

    properties = _builtin_properties(root)
    properties.update(_extract_properties(root))

    deps: list[Dependency] = []

    def _collect(dependencies_elem: ET.Element | None) -> None:
        if dependencies_elem is None:
            return
        for dep_elem in _find_children_by_local_name(dependencies_elem, "dependency"):
            group_id = _text(_find_child_by_local_name(dep_elem, "groupId"))
            artifact_id = _text(_find_child_by_local_name(dep_elem, "artifactId"))
            raw_version = _text(_find_child_by_local_name(dep_elem, "version"))
            scope = _text(_find_child_by_local_name(dep_elem, "scope"))
            classifier = _text(_find_child_by_local_name(dep_elem, "classifier"))
            if group_id is None or artifact_id is None:
                continue  # malformed <dependency>, skip rather than crash
            resolved = _resolve_property_ref(raw_version, properties)
            # If resolution didn't change anything and it wasn't a property
            # ref at all, resolved == raw_version (literal version).
            if resolved == raw_version:
                resolved_version = raw_version
            else:
                resolved_version = resolved
            deps.append(
                Dependency(
                    group_id=group_id,
                    artifact_id=artifact_id,
                    raw_version=raw_version,
                    resolved_version=resolved_version,
                    scope=scope,
                    classifier=classifier,
                )
            )

    # Top-level <dependencies>
    _collect(_find_child_by_local_name(root, "dependencies"))

    # <dependencyManagement><dependencies> is intentionally NOT walked here:
    # those entries pin versions for transitive resolution but are not
    # necessarily *used* connectors in this project, and munging the two
    # together would misrepresent what's actually declared as a direct
    # dependency.

    return ParsedPom(path=path, properties=properties, dependencies=deps)
