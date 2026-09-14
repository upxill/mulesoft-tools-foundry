import shutil
from pathlib import Path

from munit_shadow.cli import run_sync
from munit_shadow.test_file_index import NamingConvention

EXAMPLE_APP = Path(__file__).resolve().parent.parent / "examples" / "order_management_app"


def _copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "app"
    shutil.copytree(EXAMPLE_APP, dest)
    return dest


def test_sync_on_unchanged_example_app_reports_no_changes(tmp_path):
    project = _copy_example(tmp_path)
    lines = []
    results = run_sync(project, NamingConvention(), print_fn=lines.append)

    # global-config.xml defines no flows -> skipped; orders-flow.xml already
    # has a matching, up-to-date test -> no changes.
    assert all(not r.changed for r in results)
    assert lines == []  # ambient: nothing printed when nothing changed

    original = (EXAMPLE_APP / "src/test/munit/orders-flow-test.xml").read_bytes()
    after = (project / "src/test/munit/orders-flow-test.xml").read_bytes()
    assert original == after


def test_sync_adds_new_mock_when_flow_gains_a_call(tmp_path):
    project = _copy_example(tmp_path)
    flow_file = project / "src/main/mule/orders-flow.xml"
    text = flow_file.read_text(encoding="utf-8")
    assert "db:select" not in text
    new_text = text.replace(
        "<logger level=\"INFO\" message=\"Inventory check complete\" doc:name=\"Log Inventory Result\"/>",
        "<logger level=\"INFO\" message=\"Inventory check complete\" doc:name=\"Log Inventory Result\"/>\n"
        "        <db:select config-ref=\"ordersDbConfig\" doc:name=\"Lookup Customer\">"
        "<db:sql>SELECT * FROM customers</db:sql></db:select>",
    )
    assert new_text != text
    flow_file.write_text(new_text, encoding="utf-8")

    lines = []
    results = run_sync(project, NamingConvention(), print_fn=lines.append)

    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    assert "db:select" in changed[0].new_mock_operations
    assert len(lines) == 1
    assert "orders-flow.xml changed" in lines[0]
    assert "db:select" in lines[0]

    test_text = (project / "src/test/munit/orders-flow-test.xml").read_text(encoding="utf-8")
    assert 'processor="db:select"' in test_text
    # hand-written assertion still there, untouched
    assert '<munit-tools:assert-that expression="#[payload.quantityAvailable]" is="#[MunitTools::equalTo(42)]"/>' in test_text


def test_dry_run_does_not_write_the_file(tmp_path):
    project = _copy_example(tmp_path)
    flow_file = project / "src/main/mule/orders-flow.xml"
    text = flow_file.read_text(encoding="utf-8")
    flow_file.write_text(
        text.replace(
            "</flow>",
            "        <db:select config-ref=\"ordersDbConfig\" doc:name=\"Lookup Customer\">"
            "<db:sql>SELECT 1</db:sql></db:select>\n    </flow>",
        ),
        encoding="utf-8",
    )
    before = (project / "src/test/munit/orders-flow-test.xml").read_bytes()

    results = run_sync(project, NamingConvention(), dry_run=True, print_fn=lambda *_: None)

    assert any(r.changed for r in results)
    after = (project / "src/test/munit/orders-flow-test.xml").read_bytes()
    assert before == after  # dry-run must not touch disk


def test_generates_brand_new_test_file_when_none_exists(tmp_path):
    project = _copy_example(tmp_path)
    (project / "src/test/munit/orders-flow-test.xml").unlink()

    results = run_sync(project, NamingConvention(), print_fn=lambda *_: None)

    created = [r for r in results if r.new_file_created]
    assert len(created) == 1
    assert (project / "src/test/munit/orders-flow-test.xml").exists()


def test_malformed_flow_file_reports_error_without_crashing(tmp_path):
    project = _copy_example(tmp_path)
    (project / "src/main/mule/orders-flow.xml").write_text("<mule><flow name=\"x\"></mule>", encoding="utf-8")

    results = run_sync(project, NamingConvention(), print_fn=lambda *_: None)

    errored = [r for r in results if r.error]
    assert len(errored) == 1
