import hashlib
import json
import os
import platform
import re
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


def get_desktop_user_agent() -> str:
    """Returns a realistic desktop User-Agent string matching the host platform."""
    sys_name = platform.system()
    if sys_name == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    elif sys_name == "Linux":
        return (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )


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
        self._last_screenshot_hash: Optional[str] = None
        self._last_screenshot_path: Optional[str] = None

    def start(self, initial_url: str) -> Page:
        """Launches Chromium with WebMCP flags and opens the initial URL if not already started."""
        if not self._pw or not self._page or self._page.is_closed():
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=not self.headed,
                args=[
                    "--enable-features=WebMCP,WebMCPTesting",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 960},
                user_agent=get_desktop_user_agent(),
                locale="en-US",
            )
            self._context.on("page", self._on_page_opened)
            self._page = self._context.new_page()
            try:
                self._page.on("close", self._on_page_closed)
            except Exception:
                pass
            if initial_url:
                try:
                    self._page.goto(initial_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    try:
                        self._page.goto(initial_url, timeout=10000)
                    except Exception:
                        pass
        else:
            if initial_url and self._page and not self._page.is_closed():
                cur = self._page.url.rstrip("/")
                init = initial_url.rstrip("/")
                if cur != init and not any(t.get("url", "").rstrip("/") == init for t in self.get_tabs()):
                    try:
                        self._page.goto(initial_url, wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        try:
                            self._page.goto(initial_url, timeout=10000)
                        except Exception:
                            pass
        return self._page

    def navigate_to(self, url: str) -> bool:
        """Navigates the active page to a specific URL."""
        if not self._page or self._page.is_closed():
            return False
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=12000)
            return True
        except Exception:
            try:
                self._page.goto(url, timeout=12000)
                return True
            except Exception:
                return False

    def _on_page_opened(self, page: Page):
        """Automatically tracks and focuses newly opened tabs (target='_blank', window.open, popups)."""
        try:
            page.wait_for_load_state("domcontentloaded", timeout=4000)
        except Exception:
            pass
        self._page = page
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            page.on("close", self._on_page_closed)
        except Exception:
            pass

    def _on_page_closed(self, closed_page: Page):
        """Refocuses remaining active tab when a tab is closed."""
        if self._context:
            remaining = [p for p in self._context.pages if not p.is_closed() and p != closed_page]
            if remaining:
                if self._page == closed_page or self._page is None or self._page.is_closed():
                    self._page = remaining[-1]
                    try:
                        self._page.bring_to_front()
                    except Exception:
                        pass
            else:
                self._page = None

    def new_tab(self, url: str = "") -> Page:
        """Opens a new browser tab/page, focuses it, and optionally navigates to url."""
        if not self._pw or not self._browser or not self._context:
            return self.start(url)
        new_p = self._context.new_page()
        self._page = new_p
        if url:
            try:
                new_p.goto(url, wait_until="domcontentloaded", timeout=12000)
            except Exception:
                try:
                    new_p.goto(url, timeout=12000)
                except Exception:
                    pass
        return new_p

    def get_tabs(self) -> List[Dict[str, Any]]:
        """Returns all open browser tabs with index, URL, title, and active flag."""
        if not self._context:
            return []
        tabs = []
        for idx, p in enumerate(self._context.pages):
            if not p.is_closed():
                u = p.url
                t = ""
                try:
                    t = p.title()
                except Exception:
                    pass
                tabs.append({
                    "index": idx,
                    "url": u,
                    "title": t,
                    "active": (p == self._page),
                })
        return tabs

    def switch_tab(self, target: Union[int, str, Any]) -> bool:
        """
        Switches the active tab by tab index (int), URL/domain keyword (str), or Page object.
        Brings the target tab to the front.
        """
        if not self._context:
            return False
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not pages:
            return False

        if isinstance(target, int):
            if 0 <= target < len(pages):
                self._page = pages[target]
                try:
                    self._page.bring_to_front()
                except Exception:
                    pass
                return True
            return False

        if isinstance(target, str):
            kw = target.lower().strip()
            # 1. Exact or partial domain match
            for p in pages:
                p_netloc = urlparse(p.url).netloc.lower()
                p_url = p.url.lower()
                if kw in p_netloc or kw in p_url:
                    self._page = p
                    try:
                        self._page.bring_to_front()
                    except Exception:
                        pass
                    return True
            # 2. Page title match
            for p in pages:
                try:
                    p_title = p.title().lower()
                    if kw in p_title:
                        self._page = p
                        try:
                            self._page.bring_to_front()
                        except Exception:
                            pass
                        return True
                except Exception:
                    pass
            return False

        if hasattr(target, "bring_to_front") and target in pages:
            self._page = target
            try:
                self._page.bring_to_front()
            except Exception:
                pass
            return True

        return False

    def close_tab(self, target: Optional[Union[int, str, Any]] = None) -> bool:
        """Closes a specific tab (or active tab) and refocuses remaining tab."""
        if not self._context:
            return False
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not pages:
            return False

        page_to_close = self._page
        if target is not None:
            if isinstance(target, int) and 0 <= target < len(pages):
                page_to_close = pages[target]
            elif isinstance(target, str):
                kw = target.lower().strip()
                for p in pages:
                    if kw in urlparse(p.url).netloc.lower() or kw in p.url.lower():
                        page_to_close = p
                        break
            elif hasattr(target, "close"):
                page_to_close = target

        if page_to_close:
            try:
                page_to_close.close()
            except Exception:
                pass
            remaining = [p for p in self._context.pages if not p.is_closed()]
            if remaining:
                self._page = remaining[-1]
                try:
                    self._page.bring_to_front()
                except Exception:
                    pass
            else:
                self._page = None
            return True
        return False

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not started. Call driver.start(url) first.")
        return self._page

    @property
    def current_url(self) -> str:
        return self.get_active_url()

    def get_active_url(self) -> str:
        if not self._page or self._page.is_closed():
            return ""
        try:
            return self.page.url
        except Exception:
            return ""

    def get_page_title(self) -> str:
        if not self._page or self._page.is_closed():
            return ""
        try:
            return self.page.title()
        except Exception:
            return ""

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
                # Bounded: a declarative form tool that never responds would otherwise block 30 s.
                return self.page.webmcp.call_tool(tool_name, params, timeout=5000)  # type: ignore
            except Exception:
                pass

        # 2. DOM modelContext / declarative form execution
        js_code = """
        async ({ name, params }) => {
            const ctx = window.navigator?.modelContext || window.document?.modelContext;
            let execError = null;
            // Native executeTool on a declarative form fills it and then waits for a submit that
            // never comes. Bound every native await; on timeout fall through to the declarative
            // fallback below, which fills and submits the form itself.
            const TIMEOUT = Symbol('timeout');
            const bounded = (p, ms) => Promise.race([p, new Promise(r => setTimeout(() => r(TIMEOUT), ms))]);
            if (ctx && typeof ctx.getTools === 'function') {
                try {
                    const tools = await bounded(ctx.getTools(), 3000);
                    const tool = tools === TIMEOUT ? null : tools.find(t => t.name === name);
                    if (tool) {
                        const out = await bounded(ctx.executeTool(tool, params), 5000);
                        if (out !== TIMEOUT) return out;
                        if (!document.querySelector(`form[toolname="${name}"]`)) {
                            return { status: 'timeout', tool: name };
                        }
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
        try:
            self.dismiss_transient_overlays()
        except Exception:
            pass
        js_code = r"""
        () => {
            const elements = [];
            const seen = new Set();
            const clickable = Array.from(document.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="combobox"], [role="option"], [role="menuitem"], [data-radix-collection-item]'));
            
            for (const el of clickable) {
                // Skip button children inside links to prevent duplicate navbar elements
                if (el.tagName === 'BUTTON' && el.closest('a')) continue;

                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0 || rect.left < -50 || rect.top < -50) continue;

                const isTableRow = el.closest('tr, [data-row], .card, li');
                const isPagination = el.closest('nav[aria-label*="pagination" i], .pagination, [data-pagination]') || (el.innerText || '').toLowerCase().includes('next') || (el.innerText || '').toLowerCase().includes('previous');
                const isInViewport = (rect.bottom >= 0 && rect.top <= window.innerHeight && rect.right >= 0 && rect.left <= window.innerWidth);
                if (!isInViewport && (!isTableRow && !isPagination || rect.top > 8000)) continue;
                if ((el.disabled && !isPagination) || el.type === 'hidden') continue;
                
                let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('title') || el.name || el.id || '').replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim();

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

                if (!text && (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button')) {
                    text = el.getAttribute('aria-label') || el.getAttribute('title') || '';
                    if (!text) {
                        const svg = el.querySelector('svg');
                        if (svg) {
                            text = svg.getAttribute('aria-label') || svg.getAttribute('title') || '';
                            if (!text) {
                                const titleEl = svg.querySelector('title');
                                if (titleEl) text = titleEl.textContent || '';
                            }
                        }
                    }
                    if (!text) {
                        const cls = (el.className || '') + ' ' + (el.querySelector('svg')?.getAttribute('class') || '');
                        let actionName = '';
                        if (/cancel|close|reject|dismiss/i.test(cls)) actionName = 'Cancel';
                        else if (/delete|trash|remove/i.test(cls)) actionName = 'Delete';
                        else if (/check|approve|confirm|accept/i.test(cls)) actionName = 'Approve';
                        else if (/edit|pencil|modify/i.test(cls)) actionName = 'Edit';

                        const row = el.closest('tr, li, [data-row], .card, [role="row"]');
                        const rowText = row ? row.innerText.trim() : '';
                        const firstToken = rowText ? rowText.split(/\s+/)[0].slice(0, 15).trim() : '';

                        if (actionName && firstToken) text = `${actionName} ${firstToken}`;
                        else if (actionName) text = actionName;
                        else if (firstToken) text = `Action ${firstToken}`;
                    }
                }
                if (!text && el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA') continue;
                
                const x = Math.round(rect.left + rect.width / 2);
                const y = Math.round(rect.top + rect.height / 2);
                if (x < 0 || y < 0) continue;
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
                    text: text.slice(0, 80),
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
            elems = self.page.evaluate(js_code) or []
            if not elems and not self.page.is_closed():
                try:
                    self.page.wait_for_timeout(800)
                    elems = self.page.evaluate(js_code) or []
                except Exception:
                    pass
            return elems
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

    def _raw_click(self, target: str = "", x: Optional[float] = None, y: Optional[float] = None) -> bool:
        """Internal Playwright native click without retry."""
        clean_target = (target or "").strip()
        if clean_target:
            # 1. Try Playwright text/button/role locator with built-in auto-scroll
            for loc_str in (
                f"button:has-text({json.dumps(clean_target)})",
                f"a:has-text({json.dumps(clean_target)})",
                f"[role='button']:has-text({json.dumps(clean_target)})",
                f"[role='tab']:has-text({json.dumps(clean_target)})",
                f"[role='checkbox']:has-text({json.dumps(clean_target)})",
                f"[role='menuitem']:has-text({json.dumps(clean_target)})",
                f"label:has-text({json.dumps(clean_target)})",
                f"[aria-label*={json.dumps(clean_target)} i]",
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
                    if any(w in clean_target.lower() for w in ("cancel", "reject", "delete", "remove")):
                        btn_cand = row_loc.locator("button[aria-label*='cancel' i], button[aria-label*='reject' i], button[aria-label*='delete' i], button:has-text('Cancel'), button:has-text('Reject'), button:has-text('Delete')").first
                        if btn_cand.count() > 0:
                            btn = btn_cand
                    elif any(w in clean_target.lower() for w in ("approve", "confirm", "accept", "check")):
                        btn_cand = row_loc.locator("button[aria-label*='approve' i], button[aria-label*='confirm' i], button:has-text('Approve'), button:has-text('Confirm')").first
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

    def click(self, target: str = "", x: Optional[float] = None, y: Optional[float] = None) -> bool:
        """
        Playwright native click: locates target anywhere in document, auto-scrolls into view,
        verifies actionability, and clicks.
        If blocked or failed, attempts transient overlay / popup dismissal and retries once.
        """
        pages_before = len(self._context.pages) if self._context else 1
        res = self._raw_click(target=target, x=x, y=y)
        if not res:
            try:
                dismissed = self.dismiss_transient_overlays()
                if dismissed or self._has_blocking_overlay():
                    time.sleep(0.15)
                    res = self._raw_click(target=target, x=x, y=y)
            except Exception:
                pass

        # Check if click spawned a new tab/window (target="_blank" or window.open)
        if self._context:
            pages = [p for p in self._context.pages if not p.is_closed()]
            if len(pages) > pages_before:
                self._page = pages[-1]
                try:
                    self._page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
                try:
                    self._page.bring_to_front()
                except Exception:
                    pass
        return res

    def _raw_fill(self, target: str = "", value: Any = "", x: Optional[float] = None, y: Optional[float] = None, press_enter: bool = False) -> bool:
        """Internal Playwright native fill without retry."""
        val_str = str(value) if value is not None else ""
        clean_target = (target or "").strip()
        if clean_target:
            variations = [clean_target]
            if "_" in clean_target:
                variations.append(clean_target.replace("_", "-"))
                variations.append(clean_target.replace("_", " "))
            if "-" in clean_target:
                variations.append(clean_target.replace("-", "_"))
                variations.append(clean_target.replace("-", " "))

            # Try Playwright locators for inputs across target variations
            for var in variations:
                for loc_str in (
                    f"input[name={json.dumps(var)}]",
                    f"input#{var}",
                    f"input[placeholder*={json.dumps(var)} i]",
                    f"textarea[name={json.dumps(var)}]",
                    f"textarea#{var}",
                    f"textarea[placeholder*={json.dumps(var)} i]",
                    f"input[aria-label*={json.dumps(var)} i]",
                    f"input[title*={json.dumps(var)} i]",
                    f"input[data-testid*={json.dumps(var)} i]",
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

                # Also try get_by_label and get_by_placeholder
                try:
                    by_lbl = self.page.get_by_label(var, exact=False).first
                    if by_lbl.count() > 0:
                        by_lbl.scroll_into_view_if_needed(timeout=1000)
                        by_lbl.fill(val_str, timeout=1500)
                        if press_enter:
                            by_lbl.press("Enter")
                        return True
                except Exception:
                    pass

                try:
                    by_ph = self.page.get_by_placeholder(var, exact=False).first
                    if by_ph.count() > 0:
                        by_ph.scroll_into_view_if_needed(timeout=1000)
                        by_ph.fill(val_str, timeout=1500)
                        if press_enter:
                            by_ph.press("Enter")
                        return True
                except Exception:
                    pass

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

    def fill(self, target: str = "", value: Any = "", x: Optional[float] = None, y: Optional[float] = None, press_enter: bool = False) -> bool:
        """
        Playwright native fill: locates input/form field by placeholder, label, name, or id,
        auto-scrolls into view, clears, and fills value.
        If blocked or failed, attempts transient overlay / popup dismissal and retries once.
        """
        res = self._raw_fill(target=target, value=value, x=x, y=y, press_enter=press_enter)
        if not res:
            try:
                dismissed = self.dismiss_transient_overlays()
                if dismissed or self._has_blocking_overlay():
                    time.sleep(0.15)
                    res = self._raw_fill(target=target, value=value, x=x, y=y, press_enter=press_enter)
            except Exception:
                pass
        return res

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
                sel = self.page.locator(f"select[name*={json.dumps(clean_target)} i], select#{clean_target}, select[aria-label*={json.dumps(clean_target)} i]").first
                if sel.count() > 0:
                    try:
                        sel.select_option(label=clean_value)
                        return True
                    except Exception:
                        sel.select_option(value=clean_value)
                        return True
            except Exception:
                pass

        try:
            sel_opt = self.page.locator(f"select:has(option:has-text({json.dumps(clean_value)}))").first
            if sel_opt.count() > 0:
                sel_opt.select_option(label=clean_value)
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

    def _has_blocking_overlay(self) -> bool:
        """Checks if a modal, dialog, or overlay backdrop is active on screen."""
        if not self._page or self._page.is_closed():
            return False
        try:
            return self.page.locator("[role='dialog'], [role='alertdialog'], [aria-modal='true'], .modal.show, div[class*='backdrop' i], div[class*='overlay' i]:has(button)").count() > 0
        except Exception:
            return False

    def dismiss_transient_overlays(self) -> bool:
        """Dismisses generic blocking popups, login modals, promo dialogs, or cookie banners if present."""
        if not self._page or self._page.is_closed():
            return False
        dismiss_selectors = [
            # Standard close icons & glyphs (cross, multiplication, letter x)
            "button:has-text('✕')",
            "button:has-text('×')",
            "button:has-text('✕ ')",
            "button:has-text('✕')",
            "span:has-text('✕')",
            "span:has-text('×')",
            "[aria-label*='close' i]",
            "[title*='close' i]",
            "[data-dismiss='modal']",
            "[data-bs-dismiss='modal']",
            # Common e-commerce & web modals (Flipkart login wall, Amazon address, Quora/Medium popups)
            "button._2KpZ6l._2doB4z",
            "button._2doB4z",
            "span._30XB9k",
            "div._2QfC02 button",
            "span[role='button']:has-text('✕')",
            "span[role='button']:has-text('×')",
            "button[class*='close' i]",
            "button[class*='dismiss' i]",
            # Common promo/newsletter/auth action buttons
            "button:has-text('Not now')",
            "button:has-text('Maybe later')",
            "button:has-text('No thanks')",
            "button:has-text('Skip')",
            "button:has-text('Dismiss')",
            "button:has-text('Continue without')",
            "button:has-text('Continue shopping')",
            "a:has-text('Continue shopping')",
            "button:has-text('Stay on web')",
            # Cookie and GDPR consent
            "button:has-text('Accept all')",
            "button:has-text('Accept all cookies')",
            "button:has-text('Accept Cookies')",
            "button:has-text('Allow all')",
            "button:has-text('I Agree')",
            "button:has-text('Got it')",
            "button:has-text('I understand')",
            # Dialog-scoped buttons
            "[role='dialog'] button:has-text('Close')",
            "[role='dialog'] [aria-label*='close' i]",
            "[role='dialog'] span[role='button']",
            "[role='alertdialog'] button:has-text('Close')",
            "[role='alertdialog'] [aria-label*='close' i]",
        ]
        dismissed = False
        for sel in dismiss_selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=600)
                    dismissed = True
                    time.sleep(0.15)
                    break
            except Exception:
                continue

        # If an active modal/dialog is still present, try pressing Escape key
        try:
            if self._has_blocking_overlay():
                self.page.keyboard.press("Escape")
                time.sleep(0.1)
                dismissed = True
        except Exception:
            pass

        return dismissed

    def wait_for_settled(self, timeout_ms: int = 2000):
        """Waits for network and URL to settle after an action."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            self.page.wait_for_load_state("networkidle", timeout=min(300, timeout_ms))
        except Exception:
            pass
        try:
            self.dismiss_transient_overlays()
        except Exception:
            pass
        if self._context:
            pages = [p for p in self._context.pages if not p.is_closed()]
            if pages and (self._page is None or self._page.is_closed() or self._page not in pages):
                self._page = pages[-1]
                try:
                    self._page.bring_to_front()
                except Exception:
                    pass

    def wait_for_page_interactive(self, timeout_ms: int = 2000):
        """Waits for DOM and primary interactive elements to become actionable."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        try:
            self.dismiss_transient_overlays()
        except Exception:
            pass

    def take_screenshot(self, filepath: Optional[str] = None, full_page: bool = True, force: bool = False) -> str:
        """Captures a screenshot of the current page and returns saved file path.
        
        If the captured screenshot byte content is identical to the last captured screenshot,
        avoids writing duplicate files to disk and returns the previous screenshot path.
        """
        if not filepath:
            os.makedirs("reports", exist_ok=True)
            filepath = os.path.join("reports", f"screenshot_{int(time.time() * 1000)}.png")
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        try:
            if self._page:
                try:
                    self._page.evaluate("() => window.scrollTo(0, 0)")
                except Exception:
                    pass
            raw_bytes = self.page.screenshot(full_page=full_page)
        except Exception:
            raw_bytes = self.page.screenshot()

        cur_hash = hashlib.md5(raw_bytes).hexdigest()
        if (
            not force
            and self._last_screenshot_hash == cur_hash
            and self._last_screenshot_path
            and os.path.exists(self._last_screenshot_path)
        ):
            return self._last_screenshot_path

        with open(filepath, "wb") as f:
            f.write(raw_bytes)

        self._last_screenshot_hash = cur_hash
        self._last_screenshot_path = filepath
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
        """Closes browser session safely."""
        try:
            if self._context:
                for p in list(self._context.pages):
                    try:
                        p.close()
                    except Exception:
                        pass
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None
            self._page = None
            if self._browser:
                self._browser.close()
                self._browser = None
        except Exception:
            pass
        finally:
            try:
                if self._pw:
                    self._pw.stop()
                    self._pw = None
            except Exception:
                pass

    def stop(self):
        """Alias for close() to cleanly shut down browser regardless of caller naming convention."""
        self.close()