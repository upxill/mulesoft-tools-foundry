"""Offline tests: generate a server into a tmp dir and assert files exist and
the generated server.py is syntactically valid Python. No network required."""

import ast
import py_compile
from pathlib import Path

from mule_to_mcp import raml_parser
from mule_to_mcp.auth import AuthConfig
from mule_to_mcp.codegen import generate_project, synthesize_tool_name, tool_name_for
from mule_to_mcp.model import Operation

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
ORDERS_RAML = EXAMPLES_DIR / "orders_api.raml"


def _parsed():
    doc = raml_parser.load_raml(ORDERS_RAML.read_text(encoding="utf-8"))
    return doc, raml_parser.extract_operations(doc)


def test_synthesize_tool_name_from_method_and_path():
    op = Operation(operation_id="", method="get", path="/orders/{orderId}")
    assert synthesize_tool_name(op) == "get_orders_by_order_id"


def test_tool_name_for_dedupes_collisions():
    op1 = Operation(operation_id="", method="get", path="/a")
    op2 = Operation(operation_id="", method="get", path="/a")
    used: set[str] = set()
    name1 = tool_name_for(op1, used)
    name2 = tool_name_for(op2, used)
    assert name1 == "get_a"
    assert name2 == "get_a_2"


def test_generate_project_writes_expected_files(tmp_path):
    doc, operations = _parsed()
    out_dir = tmp_path / "generated"
    auth = AuthConfig(mode="client-id", client_id_env="ORDERS_CLIENT_ID", client_secret_env="ORDERS_CLIENT_SECRET")

    generate_project(
        operations,
        title=raml_parser.get_api_title(doc),
        source=str(ORDERS_RAML),
        source_format="raml",
        base_url=raml_parser.get_base_url(doc),
        out_dir=out_dir,
        auth=auth,
    )

    assert (out_dir / "server.py").is_file()
    assert (out_dir / "pyproject.toml").is_file()
    assert (out_dir / "README.md").is_file()


def test_generated_server_is_syntactically_valid_python(tmp_path):
    doc, operations = _parsed()
    out_dir = tmp_path / "generated"
    auth = AuthConfig(mode="none")

    generate_project(
        operations,
        title=raml_parser.get_api_title(doc),
        source=str(ORDERS_RAML),
        source_format="raml",
        base_url=raml_parser.get_base_url(doc),
        out_dir=out_dir,
        auth=auth,
    )

    server_path = out_dir / "server.py"
    source = server_path.read_text(encoding="utf-8")

    ast.parse(source)  # SyntaxError otherwise
    py_compile.compile(str(server_path), doraise=True)


def test_generated_server_defines_one_tool_function_per_operation(tmp_path):
    doc, operations = _parsed()
    out_dir = tmp_path / "generated"
    auth = AuthConfig(mode="none")

    generate_project(
        operations,
        title=raml_parser.get_api_title(doc),
        source=str(ORDERS_RAML),
        source_format="raml",
        base_url=raml_parser.get_base_url(doc),
        out_dir=out_dir,
        auth=auth,
    )

    source = (out_dir / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tool_decorated_funcs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for dec in node.decorator_list
        if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "tool"
    ]
    assert set(tool_decorated_funcs) == {
        "list_orders",
        "create_order",
        "get_order_by_id",
        "update_order_status",
    }


def test_generated_server_contains_base_url_and_client_id_wiring_no_hardcoded_secret(tmp_path):
    doc, operations = _parsed()
    out_dir = tmp_path / "generated"
    auth = AuthConfig(
        mode="client-id",
        client_id_header="client_id",
        client_secret_header="client_secret",
        client_id_env="ORDERS_CLIENT_ID",
        client_secret_env="ORDERS_CLIENT_SECRET",
    )

    generate_project(
        operations,
        title=raml_parser.get_api_title(doc),
        source=str(ORDERS_RAML),
        source_format="raml",
        base_url=raml_parser.get_base_url(doc),
        out_dir=out_dir,
        auth=auth,
    )

    source = (out_dir / "server.py").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8091" in source
    assert "os.environ.get('ORDERS_CLIENT_ID')" in source
    assert "os.environ.get('ORDERS_CLIENT_SECRET')" in source
    # No literal secret value should ever be baked into generated code.
    assert "demo-client-secret" not in source


def test_generate_project_with_oauth2_mode(tmp_path):
    doc, operations = _parsed()
    out_dir = tmp_path / "generated_oauth2"
    auth = AuthConfig(mode="oauth2", bearer_token_env="MULE_TOKEN")

    generate_project(
        operations,
        title=raml_parser.get_api_title(doc),
        source=str(ORDERS_RAML),
        source_format="raml",
        base_url=raml_parser.get_base_url(doc),
        out_dir=out_dir,
        auth=auth,
    )

    source = (out_dir / "server.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "os.environ.get('MULE_TOKEN')" in source
    assert "Authorization" in source
