from pathlib import Path

import pytest

from mule_connector_audit.pom_parser import PomParseError, parse_pom

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_real_example_pom():
    parsed = parse_pom(Path(__file__).parent.parent / "examples" / "pom.xml")
    assert len(parsed.dependencies) == 6
    connectors = parsed.connectors
    assert len(connectors) == 5  # mule-tests-functional is not a connector
    coords = {c.coordinates for c in connectors}
    assert "org.mule.connectors:mule-http-connector" in coords
    assert "org.mule.connectors:mule-db-connector" in coords
    assert "org.mule.connectors:mule-sockets-connector" in coords
    assert "org.mule.connectors:mule-objectstore-connector" in coords
    assert "com.mulesoft.connectors:mule-salesforce-connector" in coords


def test_resolves_property_reference_version():
    parsed = parse_pom(Path(__file__).parent.parent / "examples" / "pom.xml")
    http = next(d for d in parsed.dependencies if d.artifact_id == "mule-http-connector")
    assert http.raw_version == "${http.connector.version}"
    assert http.version == "0.8.0-BETA.4"
    assert not http.version_was_unresolved


def test_literal_version_untouched():
    parsed = parse_pom(Path(__file__).parent.parent / "examples" / "pom.xml")
    sockets = next(d for d in parsed.dependencies if d.artifact_id == "mule-sockets-connector")
    assert sockets.version == "0.9.0"


def test_non_connector_dependency_is_excluded_from_connectors():
    parsed = parse_pom(Path(__file__).parent.parent / "examples" / "pom.xml")
    connector_ids = {c.artifact_id for c in parsed.connectors}
    assert "mule-tests-functional" not in connector_ids


def test_missing_file_raises_pomparseerror(tmp_path):
    with pytest.raises(PomParseError):
        parse_pom(tmp_path / "does-not-exist.xml")


def test_malformed_xml_raises_pomparseerror(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<project><unclosed>", encoding="utf-8")
    with pytest.raises(PomParseError):
        parse_pom(bad)


def test_wrong_root_element_raises_pomparseerror(tmp_path):
    not_a_pom = tmp_path / "not-a-pom.xml"
    not_a_pom.write_text("<mule></mule>", encoding="utf-8")
    with pytest.raises(PomParseError):
        parse_pom(not_a_pom)


def test_unresolved_property_reference_is_reported(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>g</groupId>
    <artifactId>a</artifactId>
    <version>1.0</version>
    <dependencies>
        <dependency>
            <groupId>org.mule.connectors</groupId>
            <artifactId>mule-http-connector</artifactId>
            <version>${undeclared.version}</version>
        </dependency>
    </dependencies>
</project>
""",
        encoding="utf-8",
    )
    parsed = parse_pom(pom)
    dep = parsed.dependencies[0]
    assert dep.version is None
    assert dep.version_was_unresolved


def test_pom_without_namespace_still_parses(tmp_path):
    """Some hand-edited pom.xml files omit the xmlns entirely -- should still work."""
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<?xml version="1.0"?>
<project>
    <modelVersion>4.0.0</modelVersion>
    <groupId>g</groupId>
    <artifactId>a</artifactId>
    <version>1.0</version>
    <dependencies>
        <dependency>
            <groupId>org.mule.connectors</groupId>
            <artifactId>mule-ftp-connector</artifactId>
            <version>0.9.0</version>
        </dependency>
    </dependencies>
</project>
""",
        encoding="utf-8",
    )
    parsed = parse_pom(pom)
    assert len(parsed.connectors) == 1
    assert parsed.connectors[0].version == "0.9.0"


def test_dependency_without_groupid_is_skipped_not_crashed(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>g</groupId>
    <artifactId>a</artifactId>
    <version>1.0</version>
    <dependencies>
        <dependency>
            <artifactId>mystery-artifact</artifactId>
            <version>1.0</version>
        </dependency>
    </dependencies>
</project>
""",
        encoding="utf-8",
    )
    parsed = parse_pom(pom)
    assert parsed.dependencies == []
