"""A tiny in-memory mock of the Orders API described by orders_api.raml.

Implements exactly the operations in that spec (list/create/get orders,
update order status) AND genuinely enforces Anypoint-style "Client ID
enforcement": every request must carry matching `client_id` and
`client_secret` headers, or it is rejected with 401 -- exactly like a real
Mule API gateway policy would. This makes the mule-to-mcp auth wiring
genuinely end-to-end testable with zero external accounts.

Implementation note -- why stdlib `http.server` instead of Flask:
Anypoint's real Client ID enforcement policy uses headers literally named
`client_id` / `client_secret` (underscored, not hyphenated). Flask's
built-in dev server (Werkzeug) silently DROPS any incoming HTTP header
whose name contains an underscore -- see `werkzeug.serving.make_environ`,
which does `if "_" in key: continue` as a defense against CGI/WSGI
environ-variable header-spoofing ambiguity (a header "client-id" and
"client_id" would otherwise collide once mapped to "HTTP_CLIENT_ID"). That
means a Flask app built the "obvious" way can NEVER see these headers when
run via `app.run()`, which would make the whole auth demo silently
untestable. This mock is therefore built directly on `http.server`, which
exposes raw request headers with no such stripping -- confirmed during this
project's own end-to-end verification (see README).

The expected credentials are read from the environment (so the same values
can be shared with the generated MCP server's env in the demo), defaulting
to a fixed demo pair if unset.

Run:
    python examples/orders_mock_api_server.py
    # -> listening on http://127.0.0.1:8091
"""

from __future__ import annotations

import itertools
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

EXPECTED_CLIENT_ID = os.environ.get("MOCK_EXPECTED_CLIENT_ID", "demo-client-id")
EXPECTED_CLIENT_SECRET = os.environ.get("MOCK_EXPECTED_CLIENT_SECRET", "demo-client-secret")

_id_counter = itertools.count(3)

ORDERS: dict[str, dict] = {
    "o1": {"id": "o1", "customerName": "Ada Lovelace", "status": "PENDING", "total": 42.50},
    "o2": {"id": "o2", "customerName": "Alan Turing", "status": "SHIPPED", "total": 17.00},
}

_ORDER_ID_RE = re.compile(r"^/orders/([^/]+)$")


class OrdersHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _check_auth(self) -> bool:
        """Enforce the Client ID enforcement headers exactly as a real Mule gateway would."""
        client_id = self.headers.get("client_id")
        client_secret = self.headers.get("client_secret")
        if client_id != EXPECTED_CLIENT_ID or client_secret != EXPECTED_CLIENT_SECRET:
            self._send_json(401, {"error": "Unauthorized: missing or invalid client_id/client_secret"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming convention)
        if not self._check_auth():
            return
        parts = urlsplit(self.path)
        if parts.path == "/orders":
            query = parse_qs(parts.query)
            status = (query.get("status") or [None])[0]
            orders = list(ORDERS.values())
            if status:
                orders = [o for o in orders if o["status"].lower() == status.lower()]
            self._send_json(200, orders)
            return
        m = _ORDER_ID_RE.match(parts.path)
        if m:
            order = ORDERS.get(m.group(1))
            if not order:
                self._send_json(404, {"error": "order not found"})
                return
            self._send_json(200, order)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        if self.path != "/orders":
            self._send_json(404, {"error": "not found"})
            return
        payload = self._read_json_body()
        customer_name = payload.get("customerName")
        if not customer_name:
            self._send_json(400, {"error": "customerName is required"})
            return
        order_id = f"o{next(_id_counter)}"
        order = {
            "id": order_id,
            "customerName": customer_name,
            "status": "PENDING",
            "total": payload.get("total", 0),
        }
        ORDERS[order_id] = order
        self._send_json(201, order)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        m = _ORDER_ID_RE.match(urlsplit(self.path).path)
        if not m:
            self._send_json(404, {"error": "not found"})
            return
        order = ORDERS.get(m.group(1))
        if not order:
            self._send_json(404, {"error": "order not found"})
            return
        payload = self._read_json_body()
        new_status = payload.get("status")
        if not new_status:
            self._send_json(400, {"error": "status is required"})
            return
        order["status"] = new_status
        self._send_json(200, order)

    def log_message(self, fmt: str, *args) -> None:  # quieter, structured access log
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    print("Mock Orders API listening on http://127.0.0.1:8091")
    print(f"Expecting headers client_id={EXPECTED_CLIENT_ID!r} client_secret={EXPECTED_CLIENT_SECRET!r}")
    server = ThreadingHTTPServer(("127.0.0.1", 8091), OrdersHandler)
    server.serve_forever()
