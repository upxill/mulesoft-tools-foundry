"""Offline tests for maven_client.py -- urllib is mocked, no real network call."""

import json
import socket
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from mule_connector_audit.maven_client import MavenCentralClient, MavenNetworkError


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gav_payload(docs):
    return {"response": {"docs": docs}}


def test_get_latest_version_picks_highest_by_our_comparator():
    docs = [
        {"v": "0.8.0-BETA.4", "timestamp": 1500336445000},
        {"v": "0.9.0", "timestamp": 1506613359000},
    ]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_gav_payload(docs))):
        client = MavenCentralClient(timeout=5)
        result = client.get_latest_version("org.mule.connectors", "mule-http-connector")
    assert result.found is True
    assert result.latest_version == "0.9.0"
    assert result.release_timestamp_ms == 1506613359000
    assert result.version_count == 2


def test_get_latest_version_not_found_is_not_an_error():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_gav_payload([]))):
        client = MavenCentralClient(timeout=5)
        result = client.get_latest_version("com.mulesoft.connectors", "mule-salesforce-connector")
    assert result.found is False
    assert result.latest_version is None


def test_timeout_raises_maven_network_error_not_raw_traceback():
    with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
        client = MavenCentralClient(timeout=1)
        with pytest.raises(MavenNetworkError):
            client.get_latest_version("org.mule.connectors", "mule-http-connector")


def test_url_error_raises_maven_network_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Name or service not known")):
        client = MavenCentralClient(timeout=5)
        with pytest.raises(MavenNetworkError):
            client.get_latest_version("org.mule.connectors", "mule-http-connector")


def test_http_429_raises_maven_network_error_with_clear_message():
    err = urllib.error.HTTPError(
        url="https://search.maven.org/solrsearch/select", code=429, msg="Too Many Requests", hdrs=None, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        client = MavenCentralClient(timeout=5)
        with pytest.raises(MavenNetworkError, match="rate-limited"):
            client.get_latest_version("org.mule.connectors", "mule-http-connector")


def test_malformed_json_raises_maven_network_error():
    class _BadJsonResponse(_FakeResponse):
        def read(self):
            return b"not json at all {{{"

    with patch("urllib.request.urlopen", return_value=_BadJsonResponse({})):
        client = MavenCentralClient(timeout=5)
        with pytest.raises(MavenNetworkError):
            client.get_latest_version("org.mule.connectors", "mule-http-connector")


def test_unexpected_response_shape_raises_maven_network_error():
    with patch("urllib.request.urlopen", return_value=_FakeResponse({"unexpected": "shape"})):
        client = MavenCentralClient(timeout=5)
        with pytest.raises(MavenNetworkError):
            client.get_latest_version("org.mule.connectors", "mule-http-connector")
