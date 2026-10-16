"""Playwright WebMCP Browser Driver."""

from typing import Any, Dict, List, Optional, Tuple
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


class PlaywrightWebMcpDriver:
    """Manages browser lifecycle, WebMCP tool discovery, and programmatic execution.
    
    Supports both headed (interactive / visible) and headless (background / CI) modes.
    """

    def __init__(self, headed: bool = True):
        self.headed = bool(headed)
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
        try:
            self._page.goto(initial_url, wait_until="domcontentloaded", timeout=10000)
        except Exception:
            try:
                self._page.goto(initial_url, timeout=10000)
            except Exception:
                pass
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

        # 2. Browser DOM modelContext evaluation & declarative form inspection
        js_code = """
        async () => {
            const list = [];
            const seen = new Set();

            // Imperative WebMCP via modelContext
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            if (ctx && typeof ctx.getTools === 'function') {
                try {
                    const tools = await ctx.getTools();
                    for (const t of tools) {
                        seen.add(t.name);
                        list.push({
                            name: t.name,
                            description: t.description || '',
                            inputSchema: t.inputSchema || {}
                        });
                    }
                } catch (e) {}
            }

            // Declarative WebMCP forms: <form toolname="...">
            const forms = document.querySelectorAll('form[toolname]');
            forms.forEach((f) => {
                const name = f.getAttribute('toolname');
                if (name && !seen.has(name)) {
                    seen.add(name);
                    const desc = f.getAttribute('tooldescription') || `Form tool: ${name}`;
                    const props = {};
                    const req = [];
                    const inputs = f.querySelectorAll('input[name], select[name], textarea[name]');
                    inputs.forEach((inp) => {
                        const paramName = inp.getAttribute('name');
                        if (paramName) {
                            props[paramName] = {
                                type: 'string',
                                description: inp.getAttribute('toolparamdescription') || inp.getAttribute('placeholder') || ''
                            };
                            if (inp.hasAttribute('required')) req.push(paramName);
                        }
                    });
                    list.push({
                        name: name,
                        description: desc,
                        inputSchema: { type: 'object', properties: props, required: req },
                        isDeclarativeForm: true,
                        formId: f.id || ''
                    });
                }
            });

            return list;
        }
        """
        try:
            tools = self.page.evaluate(js_code)
            return tools or []
        except Exception:
            return []

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Executes a WebMCP tool on the active page (supports imperative and declarative)."""
        # 1. Native Playwright page.webmcp if available
        if hasattr(self.page, "webmcp"):
            try:
                return self.page.webmcp.call_tool(tool_name, params)  # type: ignore
            except Exception:
                pass

        # 2. DOM modelContext / declarative form execution
        js_code = """
        async ({ name, params }) => {
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            let execError = null;
            if (ctx && typeof ctx.getTools === 'function') {
                try {
                    const tools = await ctx.getTools();
                    const tool = tools.find(t => t.name === name);
                    if (tool) {
                        return await ctx.executeTool(tool, params);
                    }
                } catch (e) {
                    execError = e;
                }
            }

            // Fallback: check declarative form matching toolname
            const form = document.querySelector(`form[toolname="${name}"]`);
            if (form) {
                for (const [key, val] of Object.entries(params)) {
                    const field = form.querySelector(`[name="${key}"], #${key}, [id$="-${key}"]`);
                    if (field) {
                        field.value = String(val);
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
                const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]') || form.querySelector('button');
                if (submitBtn) {
                    submitBtn.click();
                } else if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
                return { status: 'submitted', tool: name };
            }

            if (execError) throw execError;
            throw new Error(`Tool ${name} not found on current page`);
        }
        """
        return self.page.evaluate(js_code, {"name": tool_name, "params": params})

    def get_screen_elements(self) -> List[Dict[str, Any]]:
        """Extracts visible interactive screen elements with precise coordinates (x, y)."""
        js_code = r"""
        () => {
            const elements = [];
            const seen = new Set();
            const clickable = Array.from(document.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"], [role="tab"]'));
            
            for (const el of clickable) {
                // Skip button children inside links to prevent duplicate navbar elements
                if (el.tagName === 'BUTTON' && el.closest('a')) continue;

                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;

                const isTableRow = el.closest('tr, [data-row], .card, li');
                const isInViewport = (rect.bottom >= 0 && rect.top <= window.innerHeight && rect.right >= 0 && rect.left <= window.innerWidth);
                if (!isInViewport && (!isTableRow || rect.top > 8000)) continue;
                if (el.disabled || el.type === 'hidden') continue;
                
                let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('title') || el.name || el.id || '').trim();

                // Form label lookup for inputs / textareas / selects
                if (!text && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) {
                    if (el.labels && el.labels.length > 0) {
                        text = (el.labels[0].innerText || '').trim();
                    } else if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) text = (lbl.innerText || '').trim();
                    }
                    if (!text) {
                        text = el.placeholder || el.name || el.id || el.type || 'input';
                    }
                }

                if (!text && el.tagName === 'BUTTON') {
                    const svg = el.querySelector('svg');
                    const cls = (el.className || '') + ' ' + (svg ? (svg.getAttribute('class') || '') : '');
                    let actionName = '';
                    if (cls.includes('lucide-x') || cls.includes('cancel') || cls.includes('close')) actionName = 'Cancel';
                    else if (cls.includes('lucide-trash') || cls.includes('delete')) actionName = 'Delete';
                    else if (cls.includes('lucide-check') || cls.includes('approve')) actionName = 'Approve';
                    else if (cls.includes('lucide-pencil') || cls.includes('edit')) actionName = 'Edit';

                    const row = el.closest('tr, li, [data-row], .card');
                    const rowText = row ? row.innerText.trim() : '';
                    const firstToken = rowText ? rowText.split(/\s+/)[0].slice(0, 15).trim() : '';
                    
                    // On admin orders table, if row contains both approve and reject buttons
                    if (actionName === 'Cancel' && row && row.querySelector('svg.lucide-check')) {
                        actionName = 'Reject';
                    }

                    if (actionName && firstToken) text = `${actionName} ${firstToken}`;
                    else if (actionName) text = actionName;
                    else if (firstToken) text = `Action ${firstToken}`;
                }
                if (!text && el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA') continue;
                
                const x = Math.round(rect.left + rect.width / 2);
                const y = Math.round(rect.top + rect.height / 2);
                const key = `${x},${y}`;
                if (seen.has(key)) continue;
                seen.add(key);

                elements.push({
                    tag: el.tagName.toLowerCase(),
                    type: (el.type || '').toLowerCase(),
                    text: text.slice(0, 45),
                    name: el.name || '',
                    placeholder: el.placeholder || '',
                    href: el.getAttribute('href') || '',
                    id: el.id || '',
                    disabled: Boolean(el.disabled),
                    x: x,
                    y: y,
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                });
                if (elements.length >= 60) break;
            }
            return elements;
        }
        """
        try:
            return self.page.evaluate(js_code) or []
        except Exception:
            return []

    def scroll_into_view_and_click(self, query: str) -> Optional[Tuple[int, int]]:
        """Finds any button or interactive element associated with query text across the document, scrolls into view and clicks."""
        js_code = r"""
        (query) => {
            const clean = String(query).toLowerCase().trim();
            if (!clean) return null;
            // 1. Direct button text match
            const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            for (const b of buttons) {
                if ((b.innerText || '').toLowerCase().includes(clean)) {
                    b.scrollIntoView({ behavior: 'instant', block: 'center' });
                    b.click();
                    const rect = b.getBoundingClientRect();
                    return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
                }
            }
            // 2. Row text match
            const rows = Array.from(document.querySelectorAll('tbody tr, tr, [data-row], [role="row"], li.item, .card'));
            for (const r of rows) {
                const text = (r.innerText || '').toLowerCase();
                if (text.includes(clean)) {
                    const btn = r.querySelector('button') || r.querySelector('a');
                    if (btn) {
                        btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                        btn.click();
                        const rect = btn.getBoundingClientRect();
                        return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
                    }
                }
            }
            return null;
        }
        """
        try:
            coords = self.page.evaluate(js_code, query)
            if coords and coords.get("x") is not None and coords.get("y") is not None:
                return (int(coords["x"]), int(coords["y"]))
        except Exception:
            pass
        return None

    def click_coordinate(self, x: Any, y: Any, query: Optional[str] = None):
        """Clicks directly at screen coordinates (x, y), auto-scrolling if needed, with query fallback."""
        if query:
            res = self.scroll_into_view_and_click(query)
            if res:
                return

        if x is None or y is None:
            return
        try:
            fx, fy = float(x), float(y)
            self.page.evaluate(
                """([x, y]) => {
                    let el = document.elementFromPoint(x, y);
                    if (el && el.tagName !== 'BODY' && el.tagName !== 'HTML') {
                        el.click();
                        return;
                    }
                    if (y > window.innerHeight || y < 0) {
                        window.scrollTo({ top: Math.max(0, window.pageYOffset + y - window.innerHeight / 2), behavior: 'instant' });
                    }
                    const clientY = Math.max(10, Math.min(window.innerHeight - 10, y - window.pageYOffset));
                    el = document.elementFromPoint(x, clientY);
                    if (el && el.tagName !== 'BODY' && el.tagName !== 'HTML') {
                        el.click();
                    }
                }""",
                [fx, fy],
            )
            viewport = self.page.viewport_size or {"height": 800}
            target_y = min(fy, float(viewport.get("height", 800)) - 10) if fy > 0 else 10.0
            self.page.mouse.click(fx, target_y)
        except Exception:
            try:
                self.page.mouse.click(float(x), float(y))
            except Exception:
                pass

    def fill_input_at(self, x: Any, y: Any, value: str, press_enter: bool = False):
        """Clicks at input coordinates and fills the value, optionally pressing Enter."""
        if x is None or y is None:
            return
        try:
            fx, fy = float(x), float(y)
            self.page.mouse.click(fx, fy)
            self.page.keyboard.press("Meta+A")
            self.page.keyboard.press("Backspace")
            self.page.keyboard.type(str(value))
            if press_enter:
                self.page.keyboard.press("Enter")
        except Exception:
            pass

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


