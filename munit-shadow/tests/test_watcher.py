import shutil
import threading
import time
from pathlib import Path

from munit_shadow.test_file_index import NamingConvention
from munit_shadow.watcher import _poll_loop

EXAMPLE_APP = Path(__file__).resolve().parent.parent / "examples" / "order_management_app"


def _copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "app"
    shutil.copytree(EXAMPLE_APP, dest)
    return dest


def test_poll_loop_detects_a_real_file_edit_and_syncs(tmp_path):
    project = _copy_example(tmp_path)
    convention = NamingConvention()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_poll_loop,
        args=(project, convention, False, "claude-opus-5", 0.05, False),
        kwargs={"_stop_event": stop_event},
        daemon=True,
    )
    thread.start()

    # Give the loop time to take its first snapshot of mtimes.
    time.sleep(0.2)

    flow_file = project / "src/main/mule/orders-flow.xml"
    text = flow_file.read_text(encoding="utf-8")
    new_text = text.replace(
        "</flow>",
        "        <db:select config-ref=\"ordersDbConfig\" doc:name=\"Lookup Customer\">"
        "<db:sql>SELECT 1</db:sql></db:select>\n    </flow>",
    )
    flow_file.write_text(new_text, encoding="utf-8")

    # Wait for at least a couple of poll cycles.
    deadline = time.time() + 5.0
    test_file = project / "src/test/munit/orders-flow-test.xml"
    while time.time() < deadline:
        if 'processor="db:select"' in test_file.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)

    stop_event.set()
    thread.join(timeout=2.0)

    final_text = test_file.read_text(encoding="utf-8")
    assert 'processor="db:select"' in final_text
    assert '<munit-tools:assert-that expression="#[payload.quantityAvailable]" is="#[MunitTools::equalTo(42)]"/>' in final_text
