"""Semver-aware(-ish) Maven version comparison.

Real Maven artifact versions are *not* strict semver -- they commonly look
like `1.7.2`, `0.8.0-BETA.4`, `2.0.0-SNAPSHOT`, `1.3.0.RELEASE`, or even
just `4`. This module implements a small, well-tested comparator that is
good enough to answer the one question this tool actually needs answered:
"is the pinned version behind the latest, and if so, by how much (major vs.
minor/patch)?" -- not a full re-implementation of Maven's
`ComparableVersion` algorithm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_NUMERIC_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")

# Rough precedence for common release-qualifier families, lowest first.
# A bare/no qualifier ("final"/"GA"/"RELEASE"/empty) always ranks highest --
# i.e. a final release is considered newer than a pre-release build sharing
# the same major.minor.patch numbers.
_QUALIFIER_FAMILY_RANK = {
    "snapshot": 10,
    "alpha": 20,
    "a": 20,
    "beta": 30,
    "b": 30,
    "milestone": 35,
    "m": 35,
    "cr": 40,
    "rc": 40,
}
_FINAL_QUALIFIERS = {"", "final", "ga", "release", "release-notes"}


@dataclass(frozen=True)
class ParsedVersion:
    numeric: tuple[int, ...]
    qualifier: str  # normalized (lowercased), "" if none / a "final" synonym
    raw: str

    @property
    def rank_tuple(self) -> tuple:
        """A tuple usable for ordering: bigger means newer."""
        return (self.numeric, _qualifier_rank(self.qualifier))


_FINAL_RANK = 1_000_000
_UNKNOWN_QUALIFIER_RANK = 500  # below every known pre-release family, above nothing


def _qualifier_rank(qualifier: str) -> int:
    q = qualifier.lower().strip()
    if q in _FINAL_QUALIFIERS:
        return _FINAL_RANK
    # Match a known family prefix, optionally followed by digits (rc1, beta.4, m2, ...)
    m = re.match(r"^([a-z]+)[.\-]?(\d*)$", q)
    if m:
        family, num = m.group(1), m.group(2)
        if family in _QUALIFIER_FAMILY_RANK:
            base = _QUALIFIER_FAMILY_RANK[family]
            suffix = int(num) if num else 0
            # base*1000+suffix keeps every known pre-release family's rank
            # (max ~40999) well below _FINAL_RANK, so a final release always
            # outranks any pre-release regardless of family/build number.
            return base * 1000 + suffix
    # Unknown qualifier family: treat as "some kind of pre-release", below
    # any final release but above nothing else we can reason about.
    return _UNKNOWN_QUALIFIER_RANK


def parse_version(version: str) -> ParsedVersion:
    """Parse a Maven-style version string into numeric + qualifier parts.

    Examples:
        "1.7.2"          -> numeric=(1,7,2), qualifier=""
        "0.8.0-BETA.4"   -> numeric=(0,8,0), qualifier="beta.4"
        "2.0.0-SNAPSHOT" -> numeric=(2,0,0), qualifier="snapshot"
        "1.3.0.RELEASE"  -> numeric=(1,3,0), qualifier="release"
        "4"              -> numeric=(4,0,0), qualifier=""
    """
    v = (version or "").strip()
    m = _NUMERIC_PREFIX_RE.match(v)
    if not m:
        # No leading numeric part at all (e.g. a bare qualifier or garbage
        # string) -- treat the whole thing as numeric-less so it still sorts
        # predictably rather than raising.
        return ParsedVersion(numeric=(0, 0, 0), qualifier=v.lower(), raw=version)

    numeric_part, rest = m.groups()
    nums = [int(x) for x in numeric_part.split(".")]
    while len(nums) < 3:
        nums.append(0)
    qualifier = rest.strip()
    qualifier = qualifier.lstrip(".-")
    return ParsedVersion(numeric=tuple(nums[:3]), qualifier=qualifier.lower(), raw=version)


class Staleness(str, Enum):
    UP_TO_DATE = "up-to-date"
    MINOR_BEHIND = "minor-behind"
    MAJOR_BEHIND = "major-behind"
    NOT_FOUND = "not-found"
    UNPINNED = "unpinned"


def classify(pinned_version: str, latest_version: str) -> Staleness:
    """Classify `pinned_version` relative to `latest_version`.

    - MAJOR_BEHIND: pinned's major number is lower than latest's.
    - MINOR_BEHIND: same major, but pinned is otherwise older (lower
      minor/patch, or the same major.minor.patch but a lower-precedence
      qualifier -- e.g. pinned is a `-BETA` of a version latest has since
      released as final).
    - UP_TO_DATE: pinned is equal to or newer than latest by this scheme
      (covers the "pinned is ahead of what Maven Central currently shows as
      latest" edge case too -- e.g. a private/patched build).
    """
    p = parse_version(pinned_version)
    l = parse_version(latest_version)

    if p.rank_tuple >= l.rank_tuple:
        return Staleness.UP_TO_DATE
    if p.numeric[0] < l.numeric[0]:
        return Staleness.MAJOR_BEHIND
    return Staleness.MINOR_BEHIND
