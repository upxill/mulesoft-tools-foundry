"""Offline tests: given a detected security scheme, assert the correct
header-injection code/config is generated. No network, no external services."""

from mule_to_mcp.auth import AuthConfig, resolve_auth_mode, render_auth_headers_function
from mule_to_mcp.model import DetectedSecurity


def test_resolve_auto_mode_prefers_detected_client_id():
    detected = DetectedSecurity(kind="client-id", client_id_header="client_id", client_secret_header="client_secret")
    assert resolve_auth_mode("auto", detected) == "client-id"


def test_resolve_auto_mode_prefers_detected_oauth2():
    detected = DetectedSecurity(kind="oauth2")
    assert resolve_auth_mode("auto", detected) == "oauth2"


def test_resolve_auto_mode_falls_back_to_none():
    detected = DetectedSecurity(kind="none")
    assert resolve_auth_mode("auto", detected) == "none"


def test_resolve_auto_mode_falls_back_to_none_for_unknown_scheme():
    detected = DetectedSecurity(kind="unknown")
    assert resolve_auth_mode("auto", detected) == "none"


def test_explicit_mode_overrides_detection():
    detected = DetectedSecurity(kind="client-id")
    assert resolve_auth_mode("none", detected) == "none"
    assert resolve_auth_mode("oauth2", detected) == "oauth2"


def test_client_id_mode_generates_two_header_injection():
    auth = AuthConfig(
        mode="client-id",
        client_id_header="client_id",
        client_secret_header="client_secret",
        client_id_env="ORDERS_CLIENT_ID",
        client_secret_env="ORDERS_CLIENT_SECRET",
    )
    code = render_auth_headers_function(auth)
    assert "os.environ.get('ORDERS_CLIENT_ID')" in code
    assert "os.environ.get('ORDERS_CLIENT_SECRET')" in code
    assert "headers['client_id'] = client_id" in code
    assert "headers['client_secret'] = client_secret" in code


def test_client_id_mode_uses_custom_header_names():
    auth = AuthConfig(
        mode="client-id",
        client_id_header="X-ANYPOINT-CLIENT-ID",
        client_secret_header="X-ANYPOINT-CLIENT-SECRET",
        client_id_env="ID_VAR",
        client_secret_env="SECRET_VAR",
    )
    code = render_auth_headers_function(auth)
    assert "headers['X-ANYPOINT-CLIENT-ID'] = client_id" in code
    assert "headers['X-ANYPOINT-CLIENT-SECRET'] = client_secret" in code


def test_oauth2_mode_generates_bearer_header():
    auth = AuthConfig(mode="oauth2", bearer_token_env="MY_TOKEN")
    code = render_auth_headers_function(auth)
    assert "os.environ.get('MY_TOKEN')" in code
    assert 'Authorization' in code
    assert "Bearer" in code


def test_none_mode_generates_empty_headers():
    auth = AuthConfig(mode="none")
    code = render_auth_headers_function(auth)
    assert "return {}" in code
    assert "os.environ" not in code


def test_invalid_mode_rejected():
    import pytest

    with pytest.raises(ValueError):
        AuthConfig(mode="bogus")
