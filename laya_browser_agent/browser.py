"""Playwright WebMCP Browser Driver."""

import json
from typing import Any, Dict, List, Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


class PlaywrightWebMcpDriver:
    """Manages browser lifecycle, WebMCP tool discovery, and programmatic execution."""

    def __init__(self, headed: bool = True):
        self.headed = headed
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def start(self, initial_url: str) -> Page:
        """Launches Chromium with WebMCP flags and opens the initial URL."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=not self.headed,
            args=[
                "--enable-features=WebMCP,WebMCPTesting",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._page.goto(initial_url, wait_until="networkidle")
        return self._page

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not started. Call driver.start(url) first.")
        return self._page

    def get_active_url(self) -> str:
        return self.page.url

    def get_page_title(self) -> str:
        return self.page.title()

    def discover_tools(self) -> List[Dict[str, Any]]:
        """Introspects WebMCP tools registered on the active webpage."""
        # 1. Native Playwright page.webmcp if available
        if hasattr(self.page, "webmcp"):
            try:
                tools = self.page.webmcp.tools()  # type: ignore
                if tools:
                    return tools
            except Exception:
                pass

        # 2. Browser DOM modelContext evaluation
        js_code = """
        async () => {
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            if (!ctx || typeof ctx.getTools !== 'function') return [];
            try {
                const list = await ctx.getTools();
                return list.map(t => ({
                    name: t.name,
                    description: t.description || '',
                    inputSchema: t.inputSchema || {}
                }));
            } catch (e) {
                return [];
            }
        }
        """
        try:
            tools = self.page.evaluate(js_code)
            return tools or []
        except Exception:
            return []

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Executes a WebMCP tool on the active page."""
        # 1. Native Playwright page.webmcp if available
        if hasattr(self.page, "webmcp"):
            try:
                return self.page.webmcp.call_tool(tool_name, params)  # type: ignore
            except Exception:
                pass

        # 2. DOM modelContext execution
        js_code = """
        async ({ name, params }) => {
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            if (!ctx) throw new Error('WebMCP modelContext not found on page');
            const tools = await ctx.getTools();
            const tool = tools.find(t => t.name === name);
            if (!tool) throw new Error(`Tool ${name} not found on current page`);
            return await ctx.executeTool(tool, params);
        }
        """
        return self.page.evaluate(js_code, {"name": tool_name, "params": params})

    def wait_for_settled(self, timeout_ms: int = 5000):
        """Waits for network and URL to settle after an action."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    def close(self):
        """Closes browser session."""
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
