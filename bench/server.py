"""Built-in HTTP Server for Tier 2 Generic Fixture Apps.

Serves 3 local fixture apps:
1. /shop/   - E-commerce SPA with WebMCP affordances and orders state
2. /admin/  - Admin table SPA without WebMCP (filters, pagination, actions)
3. /forms/  - Multi-step form wizard with overlay banner

Exposes dynamic state endpoints:
- /<app>/__state  - Returns current live app state JSON for state oracles
- /<app>/__reset  - Resets app state to default baseline
"""

import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

SITES_DIR = Path(__file__).parent / "sites"
DEFAULT_PORT = int(os.environ.get("LAYA_BENCH_PORT", "8765"))


class FixtureStateStore:
    """In-memory state store for the 3 fixture benchmark apps."""

    def __init__(self):
        self.reset_all()

    APPS = ("shop", "admin", "forms")

    @staticmethod
    def _initial_state(app_name: str) -> Dict[str, Any]:
        if app_name == "shop":
            return {
                "logged_in_user": None,
                "orders": [],
                "cart": [],
                "products": [
                    {"id": "PROD-1", "name": "Wireless Ergonomic Mouse", "price": 29.99, "stock": 15, "status": "IN_STOCK"},
                    {"id": "PROD-2", "name": "Mechanical Keyboard", "price": 89.99, "stock": 5, "status": "IN_STOCK"},
                    {"id": "PROD-3", "name": "USB-C Dual Dock", "price": 59.99, "stock": 0, "status": "OUT_OF_STOCK"},
                ],
            }
        if app_name == "admin":
            return {
                "selected_filter": "all",
                "search_query": "",
                "current_page": 1,
                "items": [
                    {"id": "USR-001", "name": "Alice Smith", "role": "Developer", "status": "PENDING"},
                    {"id": "USR-002", "name": "Bob Jones", "role": "Designer", "status": "APPROVED"},
                    {"id": "USR-003", "name": "Charlie Brown", "role": "Tester", "status": "PENDING"},
                    {"id": "USR-004", "name": "David Clark", "role": "Manager", "status": "REJECTED"},
                    {"id": "USR-005", "name": "Eva Green", "role": "Admin", "status": "APPROVED"},
                ],
            }
        if app_name == "forms":
            return {"banner_dismissed": False, "current_step": 1, "submitted": False, "data": {}}
        raise KeyError(f"Unknown fixture app: {app_name!r}")

    def reset_all(self):
        for app in self.APPS:
            self.reset(app)

    def reset(self, app_name: str):
        setattr(self, f"{app_name}_state", self._initial_state(app_name))

    def state(self, app_name: str) -> Dict[str, Any]:
        if app_name not in self.APPS:
            raise KeyError(f"Unknown fixture app: {app_name!r}")
        return getattr(self, f"{app_name}_state")


global_store = FixtureStateStore()


class FixtureRequestHandler(SimpleHTTPRequestHandler):
    """Custom request handler that serves HTML apps and state API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITES_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # State API endpoints
        if path.endswith("/__state") or path == "/__state":
            app = path.split("/")[1] if path.startswith("/") and len(path.split("/")) > 2 else "shop"
            try:
                self._send_json(global_store.state(app))
            except KeyError:
                self._send_json({"error": f"unknown app {app!r}"}, code=404)
            return

        if path.endswith("/__reset") or path == "/__reset":
            app = path.split("/")[1] if path.startswith("/") and len(path.split("/")) > 2 else "shop"
            try:
                global_store.reset(app)
            except KeyError:
                self._send_json({"error": f"unknown app {app!r}"}, code=404)
                return
            self._send_json({"status": "reset", "app": app})
            return

        # Direct root redirects to shop
        if path == "" or path == "/":
            self.send_response(302)
            self.send_header("Location", "/shop/")
            self.end_headers()
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        payload = {}
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            try:
                payload = parse_qs(body.decode("utf-8"))
            except Exception:
                pass

        if "/shop/api/login" in path:
            u = payload.get("username", ["user"])[0] if isinstance(payload.get("username"), list) else payload.get("username", "user")
            global_store.shop_state["logged_in_user"] = u
            self._send_json({"status": "ok", "user": u})
            return

        if "/shop/api/order" in path:
            prod = payload.get("product", "Wireless Ergonomic Mouse")
            qty = int(payload.get("quantity", 1))
            order = {
                "id": f"ORD-{len(global_store.shop_state['orders']) + 101}",
                "product": prod,
                "quantity": qty,
                "status": "PENDING",
            }
            global_store.shop_state["orders"].append(order)
            self._send_json({"status": "order_placed", "order": order})
            return

        if "/shop/api/cancel" in path:
            ord_id = payload.get("order_id")
            for o in global_store.shop_state["orders"]:
                if not ord_id or o.get("id") == ord_id:
                    o["status"] = "CANCELLED"
            self._send_json({"status": "cancelled", "orders": global_store.shop_state["orders"]})
            return

        if "/admin/api/action" in path:
            act = payload.get("action", "approve")
            item_id = payload.get("item_id")
            for it in global_store.admin_state["items"]:
                if not item_id or it["id"] == item_id:
                    it["status"] = "APPROVED" if act == "approve" else ("REJECTED" if act == "reject" else "DELETED")
            self._send_json({"status": "updated", "items": global_store.admin_state["items"]})
            return

        if "/admin/api/filter" in path:
            flt = payload.get("filter", "all")
            global_store.admin_state["selected_filter"] = flt
            self._send_json({"status": "filtered", "filter": flt})
            return

        if "/admin/api/page" in path:
            pg = int(payload.get("page", 1))
            global_store.admin_state["current_page"] = pg
            self._send_json({"status": "page_set", "page": pg})
            return

        if "/admin/api/search" in path:
            q = payload.get("query", "")
            global_store.admin_state["search_query"] = q
            self._send_json({"status": "searched", "query": q})
            return

        if "/forms/api/step" in path:
            st = int(payload.get("step", 1))
            global_store.forms_state["current_step"] = st
            if "data" in payload and isinstance(payload["data"], dict):
                global_store.forms_state["data"].update(payload["data"])
            self._send_json({"status": "step_updated", "step": st})
            return

        if "/forms/api/submit" in path:
            global_store.forms_state["submitted"] = True
            global_store.forms_state["data"] = payload
            self._send_json({"status": "submitted", "data": payload})
            return

        if "/forms/api/banner" in path:
            global_store.forms_state["banner_dismissed"] = True
            self._send_json({"status": "dismissed"})
            return

        self._send_json({"status": "ok"})

    def _send_json(self, data: Any, code: int = 200):
        content = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)


class FixtureServer:
    """Background fixture HTTP server controller."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        if self.httpd is not None:
            return
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), FixtureRequestHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


_global_server: Optional[FixtureServer] = None


def get_fixture_server(port: int = DEFAULT_PORT) -> FixtureServer:
    global _global_server
    if _global_server is None:
        _global_server = FixtureServer(port=port)
    return _global_server
