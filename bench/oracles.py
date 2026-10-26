"""Oracle Evaluators for Benchmark Tasks.

Validates whether an agent run truthfully accomplished its target goal
using application state assertions, WebMCP tool return inspections,
or extracted findings validations.
"""

from typing import Any, Dict, List, Tuple
import httpx


class DotDict(dict):
    """Dictionary supporting dot-notation attribute access and item access."""

    def __getattr__(self, name: str) -> Any:
        try:
            val = self[name]
        except KeyError:
            raise AttributeError(f"'DotDict' object has no attribute '{name}'")
        if isinstance(val, dict) and not isinstance(val, DotDict):
            val = DotDict(val)
            self[name] = val
        elif isinstance(val, list):
            val = [DotDict(x) if isinstance(x, dict) and not isinstance(x, DotDict) else x for x in val]
            self[name] = val
        return val

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(f"'DotDict' object has no attribute '{name}'")


def wrap_dotdict(val: Any) -> Any:
    """Recursively wraps dictionaries into DotDict for robust dot-access."""
    if isinstance(val, dict):
        return DotDict({k: wrap_dotdict(v) for k, v in val.items()})
    elif isinstance(val, list):
        return [wrap_dotdict(x) for x in val]
    return val


SAFE_BUILTINS = {
    "len": len,
    "all": all,
    "any": any,
    "min": min,
    "max": max,
    "sum": sum,
    "str": str,
    "int": int,
    "bool": bool,
    "float": float,
}


class BenchmarkOracle:
    """Evaluates task execution success against declared oracles."""

    @staticmethod
    def _check_tool_data(data: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
        """Checks tool output rows against the spec.

        Optional row selectors narrow which rows the claim applies to, so an unrelated
        pre-existing row can never satisfy the oracle:
          where:     {field: value}   exact match on row fields
          id_prefix: {field: prefix}  row field must start with prefix
        The selected set must be non-empty. With match="all" (default) EVERY selected row must
        have field == expected; with match="any" at least one must.
        """
        field_name = spec.get("field", "")
        expected = spec.get("expected")
        where = spec.get("where") or {}
        prefixes = spec.get("id_prefix") or {}
        rows = data if isinstance(data, list) else [data]
        rows = [r for r in rows if isinstance(r, dict)]
        selected = [
            r for r in rows
            if all(r.get(k) == v for k, v in where.items())
            and all(str(r.get(k, "")).startswith(str(v)) for k, v in prefixes.items())
        ]
        if not selected:
            return False, f"Independent WebMCP query: no row matched where={where} id_prefix={prefixes} ({len(rows)} rows)"
        bad = [r for r in selected if r.get(field_name) != expected]
        if spec.get("match", "all") == "any":
            if len(bad) == len(selected):
                return False, f"Independent WebMCP query: none of {len(selected)} selected rows have {field_name}={expected}"
        elif bad:
            return False, f"Independent WebMCP query: {len(bad)}/{len(selected)} selected rows have {field_name}!={expected} (e.g. {bad[0].get(field_name)})"
        return True, f"Independent WebMCP query verified {len(selected)} row(s) with {field_name}={expected}"

    @staticmethod
    def evaluate(
        oracle_spec: Dict[str, Any],
        base_url: str,
        site_route: str,
        agent: Any,
        steps_taken: List[Any],
        extracted_data: List[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        """
        Evaluates task execution against oracle specification.
        Returns (passed: bool, message: str).
        """
        if not oracle_spec or not isinstance(oracle_spec, dict):
            return False, "Oracle failed: no strict oracle specification declared"

        o_type = str(oracle_spec.get("type", "")).lower()
        check_expr = str(oracle_spec.get("check", "")).strip()

        if o_type == "state":
            if not check_expr or check_expr == "True":
                return False, "Oracle failed: empty or always-true check expression rejected"

            # Fetch application state from server
            state_url = f"{base_url.rstrip('/')}/{site_route.strip('/')}/__state"
            try:
                with httpx.Client(timeout=3.0) as client:
                    resp = client.get(state_url)
                    if resp.status_code != 200:
                        return False, f"Oracle HTTP {resp.status_code} fetching state from {state_url}"
                    state_data = resp.json()
            except Exception as e:
                return False, f"Oracle failed to query state from {state_url}: {e}"

            wrapped_state = wrap_dotdict(state_data)

            # Safely evaluate check expression with DotDict context & safe builtins
            context = {
                "state": wrapped_state,
                "orders": wrapped_state.get("orders", []),
                "items": wrapped_state.get("items", []),
                "cart": wrapped_state.get("cart", []),
                "user": wrapped_state.get("logged_in_user"),
                "submitted": wrapped_state.get("submitted", False),
                "data": wrapped_state.get("data", {}),
            }

            try:
                passed = bool(eval(check_expr, {"__builtins__": SAFE_BUILTINS}, context))
                msg = f"State check '{check_expr}' -> {'PASS' if passed else 'FAIL'}"
                return passed, msg
            except Exception as e:
                return False, f"Oracle eval error for expression '{check_expr}': {e}"

        elif o_type in ("webmcp", "app_query"):
            import socket
            from urllib.parse import urlparse

            tool_name = oracle_spec.get("tool", "")
            field_name = oracle_spec.get("field", "")
            expected_val = oracle_spec.get("expected")

            if expected_val is None:
                return False, f"Oracle failed: WebMCP check requires explicit 'expected' value for field '{field_name}'"

            if not steps_taken:
                return False, "Oracle failed: zero steps executed by agent"

            # Query the target application independently
            app_url = oracle_spec.get("url") or (base_url if base_url.startswith("http") else f"http://{base_url}")
            if site_route and not app_url.endswith(site_route):
                app_url = f"{app_url.rstrip('/')}/{site_route.strip('/')}"

            # Fast socket probe: fail immediately in <5ms if application port is offline
            parsed = urlparse(app_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            try:
                with socket.create_connection((host, port), timeout=0.15):
                    pass
            except (OSError, ConnectionRefusedError):
                return False, f"Oracle failed: application at {app_url} is unreachable / offline"

            # Independent browser query for WebMCP tool
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()
                    try:
                        page.goto(app_url, timeout=5000)
                        page.wait_for_load_state("networkidle", timeout=5000)
                        login = oracle_spec.get("login")
                        if login:
                            # Independent session: the oracle authenticates itself as the role that can read the data.
                            page.fill('input[name="username"], #username', str(login["username"]))
                            page.fill('input[name="password"], #password', str(login["password"]))
                            page.click('button[type="submit"], button:has-text("Login")')
                            page.wait_for_load_state("networkidle", timeout=5000)
                        if oracle_spec.get("path"):
                            page.goto(f"{app_url.rstrip('/')}{oracle_spec['path']}", timeout=5000)
                            page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass

                    raw = page.evaluate(f"""async () => {{
                        const ctx = window.navigator?.modelContext || window.document?.modelContext;
                        if (!ctx || !ctx.getTools) return null;
                        const tools = await ctx.getTools();
                        const targetTool = tools.find(t => t.name === "{tool_name}");
                        if (!targetTool) return null;
                        return await ctx.executeTool(targetTool, {{}});
                    }}""")
                    browser.close()

                    if raw is not None:
                        import json
                        data = json.loads(raw) if isinstance(raw, str) else raw
                        return BenchmarkOracle._check_tool_data(data, oracle_spec)
                    return False, f"Independent WebMCP query failed: tool '{tool_name}' not available on page"
            except Exception as e:
                return False, f"Independent app query failed for tool '{tool_name}': {e}"

        elif o_type == "url":
            needle = str(oracle_spec.get("contains", "")).strip().lower()
            if not needle:
                return False, "Oracle failed: empty url 'contains' rejected"
            visited = [str(getattr(s, "url", "")).lower() for s in steps_taken]
            passed = any(needle in u for u in visited)
            return passed, f"URL check for '{needle}' among {len(visited)} visited pages: {passed}"

        elif o_type == "answer":
            expected_substring = str(oracle_spec.get("substring", "")).strip().lower()
            if not expected_substring or expected_substring == "true":
                return False, "Oracle failed: empty or always-true answer substring rejected"
            all_text = " ".join([str(d) for d in extracted_data]).lower()
            passed = expected_substring in all_text
            return passed, f"Answer check for '{expected_substring}' in findings: {passed}"

        # Hard failure on missing/unknown oracle
        return False, f"Oracle failed: unsupported oracle type '{o_type}'"
