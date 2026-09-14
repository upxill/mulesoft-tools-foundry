"""Locate the MUnit test file that corresponds to a given Mule flow file.

Real Mule Maven projects (per the Maven archetype / MUnit docs, see
README.md "Project conventions confirmed") keep flow XML under
``src/main/mule/*.xml`` and MUnit test suites under ``src/test/munit/*.xml``.
The default naming convention this tool assumes:

    src/main/mule/orders-flow.xml  ->  src/test/munit/orders-flow-test.xml

Both the source subdirectory (``src/main/mule``) and destination
subdirectory (``src/test/munit``) plus the filename suffix are configurable
(``--test-dir`` / ``--test-suffix``) in case a project deviates from the
convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

DEFAULT_FLOW_DIR = "src/main/mule"
DEFAULT_TEST_DIR = "src/test/munit"
DEFAULT_TEST_SUFFIX = "-test"


@dataclass(frozen=True)
class NamingConvention:
    flow_dir: str = DEFAULT_FLOW_DIR
    test_dir: str = DEFAULT_TEST_DIR
    test_suffix: str = DEFAULT_TEST_SUFFIX


def find_flow_files(project_dir: Path, convention: NamingConvention) -> List[Path]:
    """All flow XML files under ``<project_dir>/<flow_dir>/*.xml``, sorted
    for deterministic iteration order."""
    flow_dir = Path(project_dir) / convention.flow_dir
    if not flow_dir.is_dir():
        return []
    return sorted(p for p in flow_dir.glob("*.xml") if p.is_file())


def test_file_for_flow(flow_path: Path, project_dir: Path, convention: NamingConvention) -> Path:
    """Derive the matching MUnit test file path for a given flow file, per
    the naming convention. Does not check whether the file exists yet --
    callers decide whether to create it (scaffold.py) or update it in place
    (splice.py).
    """
    project_dir = Path(project_dir)
    stem = flow_path.stem  # "orders-flow" from "orders-flow.xml"
    test_name = f"{stem}{convention.test_suffix}.xml"
    return project_dir / convention.test_dir / test_name
