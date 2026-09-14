import pytest

from mule_connector_audit.versioning import Staleness, classify, parse_version


@pytest.mark.parametrize(
    "raw,numeric,qualifier",
    [
        ("1.7.2", (1, 7, 2), ""),
        ("0.8.0-BETA.4", (0, 8, 0), "beta.4"),
        ("2.0.0-SNAPSHOT", (2, 0, 0), "snapshot"),
        ("1.3.0.RELEASE", (1, 3, 0), "release"),
        ("4", (4, 0, 0), ""),
        ("1.2", (1, 2, 0), ""),
        ("1.2.3.4", (1, 2, 3), ""),  # 4th numeric segment truncated, no qualifier
    ],
)
def test_parse_version(raw, numeric, qualifier):
    parsed = parse_version(raw)
    assert parsed.numeric == numeric
    assert parsed.qualifier == qualifier


def test_up_to_date_exact_match():
    assert classify("1.7.2", "1.7.2") == Staleness.UP_TO_DATE


def test_up_to_date_when_pinned_is_ahead():
    assert classify("2.0.0", "1.9.0") == Staleness.UP_TO_DATE


def test_minor_behind_same_major():
    assert classify("1.2.0", "1.9.0") == Staleness.MINOR_BEHIND


def test_minor_behind_patch_only():
    assert classify("1.9.0", "1.9.5") == Staleness.MINOR_BEHIND


def test_major_behind():
    assert classify("1.2.0", "2.0.0") == Staleness.MAJOR_BEHIND


def test_major_behind_multiple_majors():
    assert classify("0.9.0", "3.1.0") == Staleness.MAJOR_BEHIND


def test_prerelease_pinned_vs_final_latest_same_numeric_is_minor_behind():
    # A real, tricky case: 0.8.0-BETA.4 pinned, and the connector's own next
    # real release was 0.9.0 -- different minor, so major-untouched but
    # behind.
    assert classify("0.8.0-BETA.4", "0.9.0") == Staleness.MINOR_BEHIND


def test_prerelease_pinned_vs_final_latest_exact_same_numeric_is_minor_behind():
    # Same major.minor.patch, but pinned is a pre-release qualifier and
    # latest is the final release of that exact version -- pinned is
    # logically older even though the numeric triplet matches.
    assert classify("1.5.0-RC1", "1.5.0") == Staleness.MINOR_BEHIND


def test_snapshot_pinned_vs_final_same_numeric_is_minor_behind():
    assert classify("2.0.0-SNAPSHOT", "2.0.0") == Staleness.MINOR_BEHIND


def test_equal_qualifiers_are_up_to_date():
    assert classify("1.5.0-RC1", "1.5.0-RC1") == Staleness.UP_TO_DATE


def test_higher_rc_number_is_up_to_date_relative_to_lower():
    assert classify("1.5.0-RC2", "1.5.0-RC1") == Staleness.UP_TO_DATE


def test_lower_rc_number_is_minor_behind_relative_to_higher():
    assert classify("1.5.0-RC1", "1.5.0-RC2") == Staleness.MINOR_BEHIND


def test_real_http_connector_case_from_maven_central():
    # Real coordinates, real historical data (see README Live Verification):
    # org.mule.connectors:mule-http-connector's only two published versions
    # on Maven Central are 0.8.0-BETA.4 (2017-07-17) and 0.9.0 (2017-09-28).
    assert classify("0.8.0-BETA.4", "0.9.0") == Staleness.MINOR_BEHIND
    assert classify("0.9.0", "0.9.0") == Staleness.UP_TO_DATE
