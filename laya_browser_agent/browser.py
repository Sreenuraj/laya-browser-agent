import json
import os
import re
import time
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
        """Extracts visible interactive screen elements with precise coordinates, roles, and semantic regions."""
        js_code = r"""
        () => {
            const elements = [];
            const seen = new Set();
            const clickable = Array.from(document.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="combobox"], [role="option"], [role="menuitem"], [data-radix-collection-item]'));
            
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

                // Semantic region classification
                let region = 'content';
                if (el.closest('dialog, [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]')) {
                    region = 'overlay';
                } else if (el.closest('header, nav, [role="navigation"]')) {
                    region = 'navigation';
                } else if (el.closest('nav[aria-label*="pagination" i], .pagination, [data-pagination]')) {
                    region = 'pagination';
                } else if (el.closest('form, [role="search"], [role="toolbar"], .filters, .toolbar') || el.matches('[role="combobox"], select, input[type="text"], input[type="search"]')) {
                    region = 'toolbar';
                }

                elements.push({
                    tag: el.tagName.toLowerCase(),
                    type: (el.type || '').toLowerCase(),
                    text: text.slice(0, 45),
                    role: el.getAttribute('role') || '',
                    aria_expanded: el.getAttribute('aria-expanded') || '',
                    region: region,
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

            // Prioritize active overlays/dialogs/popovers so model operates inside them first
            elements.sort((a, b) => (a.region === 'overlay' ? -1 : (b.region === 'overlay' ? 1 : 0)));
            return elements;
        }
        """
        try:
            return self.page.evaluate(js_code) or []
        except Exception:
            return []

    def get_screen_regions(self) -> List[Dict[str, Any]]:
        """Extracts high-level semantic regions on the active page (navigation, toolbar, content, overlay, pagination)."""
        js_code = r"""
        () => {
            const regions = [];
            if (document.querySelector('dialog, [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]')) {
                regions.push({ name: 'overlay', label: 'Active modal / dialog / popover overlay' });
            }
            if (document.querySelector('header, nav, [role="navigation"]')) {
                regions.push({ name: 'navigation', label: 'Top / sidebar navigation bar' });
            }
            if (document.querySelector('form, [role="search"], [role="toolbar"], [role="combobox"], select, .filters, .toolbar')) {
                regions.push({ name: 'toolbar', label: 'Search and filter toolbar controls' });
            }
            if (document.querySelector('table, tbody, [data-row], .grid, main')) {
                regions.push({ name: 'content', label: 'Main content data grid / items list' });
            }
            const hasNext = Array.from(document.querySelectorAll('button, a')).some(b => {
                const t = (b.innerText || '').toLowerCase();
                return t.includes('next') || t.includes('previous') || t.includes('page');
            });
            if (document.querySelector('nav[aria-label*="pagination" i], .pagination') || hasNext) {
                regions.push({ name: 'pagination', label: 'Pagination controls (next/previous pages)' });
            }
            return regions;
        }
        """
        try:
            return self.page.evaluate(js_code) or []
        except Exception:
            return []

    def click(self, target: str = "", x: Optional[float] = None, y: Optional[float] = None) -> bool:
        """
        Playwright native click: locates target anywhere in document, auto-scrolls into view,
        verifies actionability, and clicks. Falls back to screen coordinates if target lookup fails.
        """
        clean_target = (target or "").strip()
        if clean_target:
            # 1. Try Playwright text/button locator with built-in auto-scroll
            for loc_str in (
                f"button:has-text({json.dumps(clean_target)})",
                f"a:has-text({json.dumps(clean_target)})",
                f"[role='button']:has-text({json.dumps(clean_target)})",
            ):
                try:
                    locator = self.page.locator(loc_str).first
                    if locator.count() > 0:
                        locator.scroll_into_view_if_needed(timeout=1000)
                        locator.click(timeout=1500)
                        return True
                except Exception:
                    continue

            # 2. Try row-associated action button (e.g. Cancel button in table row with ID)
            tok_match = re.search(r"\b([a-f0-9]{6,36})\b", clean_target.lower())
            tok = tok_match.group(1) if tok_match else ""
            search_key = tok or clean_target
            try:
                row_loc = self.page.locator(f"tr:has-text({json.dumps(search_key)}), [data-row]:has-text({json.dumps(search_key)}), li:has-text({json.dumps(search_key)})").first
                if row_loc.count() > 0:
                    btn = None
                    if any(w in clean_target.lower() for w in ("cancel", "reject", "delete")):
                        btn_cand = row_loc.locator("button:has(svg.lucide-x), button:has(svg.lucide-trash), button:has-text('Cancel'), button:has-text('Reject')").first
                        if btn_cand.count() > 0:
                            btn = btn_cand
                    elif any(w in clean_target.lower() for w in ("approve", "check")):
                        btn_cand = row_loc.locator("button:has(svg.lucide-check), button:has-text('Approve')").first
                        if btn_cand.count() > 0:
                            btn = btn_cand

                    if not btn:
                        btn = row_loc.locator("button, a").first

                    if btn and btn.count() > 0:
                        btn.scroll_into_view_if_needed(timeout=1000)
                        btn.click(timeout=1500)
                        # Check for immediate confirmation dialog (e.g. Alert Dialog confirming Approve/Reject/Delete)
                        try:
                            dialog = self.page.locator('[role="alertdialog"], [role="dialog"]').first
                            if dialog.count() > 0 and dialog.is_visible():
                                action_btn = None
                                if any(w in clean_target.lower() for w in ("approve", "check")):
                                    action_btn = dialog.locator('button:has-text("Approve"), button:has-text("Confirm"), button:has-text("Yes")').first
                                elif any(w in clean_target.lower() for w in ("reject", "cancel", "delete")):
                                    action_btn = dialog.locator('button:has-text("Reject"), button:has-text("Delete"), button:has-text("Confirm")').first
                                if action_btn and action_btn.count() > 0:
                                    action_btn.click(timeout=1000)
                        except Exception:
                            pass
                        return True
            except Exception:
                pass

        # 3. Fallback to coordinate click if target text wasn't found or coordinates supplied
        if x is not None and y is not None:
            try:
                fx, fy = float(x), float(y)
                viewport = self.page.viewport_size or {"height": 800}
                if fy > float(viewport.get("height", 800)) or fy < 0:
                    self.page.mouse.wheel(0, fy - 300)
                    self.page.wait_for_timeout(100)
                self.page.mouse.click(fx, min(fy, float(viewport.get("height", 800)) - 10))
                return True
            except Exception:
                pass

        # 4. Last resort: generic element text match
        if clean_target:
            try:
                loc = self.page.get_by_text(clean_target, exact=False).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=1000)
                    loc.click(timeout=1500)
                    return True
            except Exception:
                pass

        return False

    def fill(self, target: str = "", value: Any = "", x: Optional[float] = None, y: Optional[float] = None, press_enter: bool = False) -> bool:
        """
        Playwright native fill: locates input/form field by placeholder, label, name, or id,
        auto-scrolls into view, clears, and fills value.
        """
        val_str = str(value) if value is not None else ""
        clean_target = (target or "").strip()
        if clean_target:
            # Try Playwright locators for inputs
            for loc_str in (
                f"input[name={json.dumps(clean_target)}]",
                f"input#{clean_target}",
                f"input[placeholder*={json.dumps(clean_target)} i]",
                f"textarea[name={json.dumps(clean_target)}]",
                f"textarea#{clean_target}",
                f"input[aria-label*={json.dumps(clean_target)} i]",
            ):
                try:
                    locator = self.page.locator(loc_str).first
                    if locator.count() > 0:
                        locator.scroll_into_view_if_needed(timeout=1000)
                        locator.fill(val_str, timeout=1500)
                        if press_enter:
                            locator.press("Enter")
                        return True
                except Exception:
                    continue

        # Fallback to coordinates
        if x is not None and y is not None:
            try:
                fx, fy = float(x), float(y)
                self.page.mouse.click(fx, fy)
                self.page.keyboard.press("Meta+A")
                self.page.keyboard.press("Backspace")
                self.page.keyboard.type(val_str)
                if press_enter:
                    self.page.keyboard.press("Enter")
                return True
            except Exception:
                pass

        return False

    def select_dropdown(self, target: str = "", value: str = "") -> bool:
        """
        Selects an option from a native HTML <select> or modern custom dropdown (Radix UI / Shadcn / ARIA combobox).
        1. If native <select>, uses page.select_option.
        2. If modern combobox / dropdown:
           - Clicks the trigger matching target (e.g. 'All Status' or 'Status').
           - Waits 200ms for option popover to appear.
           - Clicks the option matching value (e.g. 'Out of Stock').
        """
        clean_target = (target or "").strip()
        clean_value = (value or "").strip()
        if not clean_value:
            return False

        # 1. Native HTML <select>
        if clean_target:
            try:
                sel = self.page.locator(f"select[name*={json.dumps(clean_target)} i], select#{clean_target}").first
                if sel.count() > 0:
                    sel.select_option(label=clean_value)
                    return True
            except Exception:
                pass

        # 2. Open combobox / dropdown trigger if not already open
        if clean_target:
            for t_str in (
                f"button[role='combobox']:has-text({json.dumps(clean_target)})",
                f"[role='combobox']:has-text({json.dumps(clean_target)})",
                f"button:has-text({json.dumps(clean_target)})",
            ):
                try:
                    cand = self.page.locator(t_str).first
                    if cand.count() > 0 and cand.is_visible():
                        cand.scroll_into_view_if_needed(timeout=1000)
                        cand.click(timeout=1500)
                        self.page.wait_for_timeout(250)
                        break
                except Exception:
                    continue

        # 3. Click the option
        for opt_str in (
            f"[role='option']:has-text({json.dumps(clean_value)})",
            f"[role='menuitem']:has-text({json.dumps(clean_value)})",
            f"[data-radix-collection-item]:has-text({json.dumps(clean_value)})",
            f"span:has-text({json.dumps(clean_value)})",
            f"text={json.dumps(clean_value)}",
        ):
            try:
                opt = self.page.locator(opt_str).first
                if opt.count() > 0 and opt.is_visible():
                    opt.scroll_into_view_if_needed(timeout=1000)
                    opt.click(timeout=1500)
                    self.page.wait_for_timeout(300)
                    return True
            except Exception:
                continue

        return False

    def mousewheel(self, dx: int = 0, dy: int = 500) -> bool:
        """Playwright native mousewheel scrolling."""
        try:
            self.page.mouse.wheel(dx, dy)
            self.page.wait_for_timeout(100)
            return True
        except Exception:
            return False

    def find(self, query: str) -> bool:
        """Playwright native element finder: checks if query text exists anywhere in the DOM."""
        try:
            return self.page.locator(f"text={query}").count() > 0
        except Exception:
            return False

    def wait_for_settled(self, timeout_ms: int = 2000):
        """Waits for network and URL to settle after an action."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            self.page.wait_for_load_state("networkidle", timeout=min(300, timeout_ms))
        except Exception:
            pass

    def take_screenshot(self, filepath: Optional[str] = None) -> str:
        """Captures a screenshot of the current page and returns saved file path."""
        if not filepath:
            os.makedirs("reports", exist_ok=True)
            filepath = os.path.join("reports", f"screenshot_{int(time.time() * 1000)}.png")
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        self.page.screenshot(path=filepath)
        return filepath

    def get_scroll_state(self) -> Dict[str, Any]:
        """Inspects whether the window or any container can scroll down or up."""
        js_code = r"""
        () => {
            const doc = document.documentElement;
            const body = document.body;
            const scrollTop = window.scrollY || doc.scrollTop || body.scrollTop || 0;
            const scrollHeight = Math.max(doc.scrollHeight, body.scrollHeight, doc.clientHeight);
            const clientHeight = window.innerHeight || doc.clientHeight;
            const winCanDown = (scrollTop + clientHeight) < (scrollHeight - 15);
            const winCanUp = scrollTop > 15;

            let containerCanDown = false;
            let containerCanUp = false;
            const scrollables = Array.from(document.querySelectorAll('div, main, section, ul, table')).filter(el => {
                const style = window.getComputedStyle(el);
                return (style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 10;
            });
            for (const el of scrollables) {
                if ((el.scrollTop + el.clientHeight) < (el.scrollHeight - 10)) containerCanDown = true;
                if (el.scrollTop > 10) containerCanUp = true;
            }

            return {
                "can_scroll_down": Boolean(winCanDown || containerCanDown),
                "can_scroll_up": Boolean(winCanUp || containerCanUp),
                "scroll_top": Math.round(scrollTop),
                "scroll_height": Math.round(scrollHeight),
                "client_height": Math.round(clientHeight)
            };
        }
        """
        try:
            return self.page.evaluate(js_code) or {"can_scroll_down": False, "can_scroll_up": False}
        except Exception:
            return {"can_scroll_down": False, "can_scroll_up": False}

    def go_back(self):
        """Navigates back in browser history."""
        self.page.go_back(wait_until="domcontentloaded")

    def reload(self):
        """Reloads current page."""
        self.page.reload(wait_until="domcontentloaded")

    def close(self):
        """Closes browser session."""
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()


