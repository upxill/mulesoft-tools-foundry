from pathlib import Path

# NOTE: test_file_for_flow is deliberately imported under a non-"test_"
# alias -- pytest's collector treats any top-level "test_*" callable found
# in a test module's namespace as a test item to run, including one merely
# *imported* into the module. Importing it under its real name here caused
# pytest to try to collect and call munit_shadow's test_file_for_flow()
# itself (with no arguments), which errored during collection.
from munit_shadow.test_file_index import NamingConvention, find_flow_files
from munit_shadow.test_file_index import test_file_for_flow as compute_test_file_path


def test_default_convention_maps_flow_to_test_file():
    convention = NamingConvention()
    flow_path = Path("/proj/src/main/mule/orders-flow.xml")
    test_path = compute_test_file_path(flow_path, Path("/proj"), convention)
    assert test_path == Path("/proj/src/test/munit/orders-flow-test.xml")


def test_custom_suffix_and_dirs():
    convention = NamingConvention(flow_dir="mule-flows", test_dir="mule-tests", test_suffix="-munit")
    flow_path = Path("/proj/mule-flows/billing.xml")
    test_path = compute_test_file_path(flow_path, Path("/proj"), convention)
    assert test_path == Path("/proj/mule-tests/billing-munit.xml")


def test_find_flow_files_lists_xml_under_flow_dir_sorted(tmp_path):
    flow_dir = tmp_path / "src/main/mule"
    flow_dir.mkdir(parents=True)
    (flow_dir / "b-flow.xml").write_text("<mule/>", encoding="utf-8")
    (flow_dir / "a-flow.xml").write_text("<mule/>", encoding="utf-8")
    (flow_dir / "notxml.txt").write_text("nope", encoding="utf-8")

    found = find_flow_files(tmp_path, NamingConvention())
    assert [p.name for p in found] == ["a-flow.xml", "b-flow.xml"]


def test_find_flow_files_missing_dir_returns_empty(tmp_path):
    found = find_flow_files(tmp_path, NamingConvention())
    assert found == []
