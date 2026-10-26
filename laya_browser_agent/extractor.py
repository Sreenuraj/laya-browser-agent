"""Universal parameter and subtask planner combining local Ollama with heuristic fallback."""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .expansion import expand_plan_from_screen, remediate_plan_from_screen
from .llm import get_llm_client, resolve_local_model

resolve_ollama_model = resolve_local_model



@dataclass
class StructuredPlan:
    """Organized input representation produced from the user's raw prompt."""
    intent: str
    slots: Dict[str, Any]
    subtasks: List[str]
    source: str = "Local LLM"
    missing_details: List[str] = field(default_factory=list)
    clarifying_question: Optional[str] = None
    confident_horizon: int = 0
    confident_subtasks: List[str] = field(default_factory=list)
    deferred_goal: Optional[str] = None


class SlotExtractor:
    """Structures human requests into parameters and sequential subtasks for the decision model."""

    def __init__(self, ollama_url: str = "http://localhost:11434", ollama_model: Optional[str] = None):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = resolve_ollama_model(ollama_model, self.ollama_url)
        self.telemetry: Dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_reads": 0,
            "cache_writes": 0,
            "latency_saved_ms": 0.0,
        }

    def extract(self, task: str, initial_url: str = "", session_context: Optional[Dict[str, Any]] = None) -> StructuredPlan:
        """Structures user input using local Ollama if running, otherwise uses instant heuristics."""
        clean_task = (task or "").replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')

        # 1. Try local Ollama (runs in ~300ms on Apple Silicon) to organize the plan
        try:
            plan = self._extract_via_ollama(clean_task, initial_url=initial_url, session_context=session_context)
            if plan and plan.subtasks:
                return plan
        except Exception:
            pass

        # 2. Deterministic 0ms heuristic fallback
        return self._extract_via_heuristics(clean_task, session_context=session_context)

    def _extract_via_ollama(self, task: str, initial_url: str = "", session_context: Optional[Dict[str, Any]] = None) -> Optional[StructuredPlan]:
        url_context = f"\nBrowser initial URL: {initial_url}" if initial_url else ""
        session_info = ""
        if session_context:
            known_user = session_context.get("slots", {}).get("username") or session_context.get("username")
            last_goal = session_context.get("last_goal") or session_context.get("goal")
            last_outcome = session_context.get("last_outcome")
            if last_outcome:
                prior_goal = last_outcome.get("goal", last_goal or "N/A")
                prior_status = last_outcome.get("status", "completed")
                prior_slots = last_outcome.get("extracted_slots", {})
                prior_data = last_outcome.get("extracted_data", [])
                prior_findings = []
                for d in prior_data:
                    m = d.get("metric", "")
                    c = d.get("count", d.get("status", ""))
                    if m and c:
                        prior_findings.append(f"{m}: {c}")
                findings_str = "; ".join(prior_findings) if prior_findings else "None"
                session_info += (
                    f"\nPrevious Turn Outcome (Turn {last_outcome.get('turn', 1)}):\n"
                    f"- Prior User Request: \"{prior_goal}\"\n"
                    f"- Execution Outcome: {prior_status}\n"
                    f"- Established Entities & Slots: {json.dumps(prior_slots)}\n"
                    f"- Findings / Values Discovered: {findings_str}\n"
                    "Multi-Turn Task Derivation Guidelines:\n"
                    "- Base the next turn plan directly on the outcome of the previous turn.\n"
                    "- Resolve anaphoric references ('it', 'them', 'that', 'the item') using established entities from the previous turn.\n"
                    "- If the request involves multiple open websites/tabs, generate symmetric, organized subtasks for each open website (e.g. 'Switch to <site1> tab' -> perform action; 'Switch to <site2> tab' -> perform action).\n"
                    "- Do NOT re-navigate or duplicate open tabs.\n"
                    "- Do NOT turn conversational constraints (e.g. 'do not close browser', 'keep tabs open') into physical action steps.\n"
                )
            elif known_user or last_goal:
                session_info = (
                    f"\nPrevious session conversation context:\n"
                    f"- Previous user request: {last_goal or 'N/A'}\n"
                    f"- Known authenticated user: {known_user or 'N/A'}\n"
                    "Note: If the current user request is a follow-up or reports unfinished work from previous steps, inherit credentials and target pages from the session context.\n"
                )
            open_tabs = session_context.get("open_tabs") or []
            if not open_tabs and last_outcome:
                open_tabs = last_outcome.get("open_tabs") or []
            if open_tabs:
                tab_lines = []
                for t in open_tabs:
                    t_idx = t.get("index", 0)
                    t_url = t.get("url", "")
                    t_title = t.get("title", "")
                    active_marker = " [ACTIVE TAB]" if t.get("active") else ""
                    tab_lines.append(f"  * Tab {t_idx}{active_marker}: {t_url} (title: '{t_title}')")
                session_info += (
                    f"\nCurrently open browser tabs:\n"
                    + "\n".join(tab_lines)
                    + "\nMulti-Tab Guidelines:\n"
                    "- The browser already has these tabs open. If the request targets websites/entities already loaded in an open tab, generate a subtask to switch to that tab ('Switch to <domain> tab') instead of opening a redundant new tab or re-navigating.\n"
                    "- Only open a new tab if the requested website is not already present in any open tab.\n"
                )
            if session_context.get("mode") == "remediation":
                reason = session_context.get("feedback_reason", "")
                session_info += (
                    f"\n⚠️ REMEDIATION & LIVE AUDIT MODE: The user reported an error or discrepancy regarding the previous run: '{reason}'.\n"
                    "- Do NOT extract numerical quantities or parameter values from the negative feedback or complaint.\n"
                    "- Structure the plan to first inspect the live screen/entity section and verify actual state, then take only the required corrective action.\n"
                    "- Do NOT repeat previous submission actions blindly.\n"
                )
        prompt = (
            "You are an autonomous web agent task planner operating under Loop Engineering.\n"
            "Analyze this human user request and return a clean, organized, structured execution plan in JSON:\n"
            "1. 'intent': concise 1-sentence normalized objective summary.\n"
            "2. 'slots': dictionary of extracted parameters (separate 'username', 'password', 'name', 'email', 'phone', 'address', 'message', 'subject', 'quantity' as int, 'search' or 'query' ONLY for text search keywords, 'status' or 'option' for dropdown / filter selections, 'dropdown' for target dropdown names). Do NOT put dropdown, status, or generic words ('products', 'items') into search/query.\n"
            "3. 'subtasks': ordered list of concise sequential action steps to achieve the goal.\n"
            "4. 'missing_details': list of essential parameters strictly missing from the user request that prevent autonomous execution (e.g. user asks to login/authenticate but gave no credentials/username/password; user asks to filter/select but gave no option/status; user asks to order/buy but gave no item name/quantity). If details are present or reasonable defaults exist, return [].\n"
            "5. 'clarifying_question': if 'missing_details' is non-empty, a single concise, polite question to ask the user for the missing details; otherwise null.\n"
            "Strict Scope Anchoring & Machine-Actionable Automation Rules:\n"
            "- Every subtask MUST anchor directly to an explicit instruction in the user request.\n"
            "- Machine-Actionable Automation Planning (NOT Human High-Level Descriptions):\n"
            "  * Automations require concrete browser actions, NOT high-level conversational prose.\n"
            "  * ONLY if the user explicitly requests searching for a keyword/item (e.g. 'search for iPhone 18 Pro Max'):\n"
            "    - Generate: 'Enter \"<clean_item_name>\" into search input box and submit'.\n"
            "    - If price/details inspection was requested: 'Inspect search results and note the price of \"<clean_item_name>\"'.\n"
            "    - Do NOT generate search steps if the user did not request a search.\n"
            "  * Clean Entities in 'slots' and 'subtasks':\n"
            "    - The search query MUST contain ONLY the exact item/entity name. Put it in 'slots.search' and 'slots.query'.\n"
            "    - NEVER embed conversational prefixes or instructions (like 'and note the price of', 'check if', 'where its cheaper') into search terms or quoted values.\n"
            "- Preserve exact destination and route names from the user request (e.g. 'my orders', 'orders', 'inventory', 'dashboard', 'command-center', 'products'). Do NOT substitute them with generic names like 'account page'.\n"
            "- For data checking or counting requests (e.g. 'check if there are any pending orders', 'count pending orders', 'how many products are out of stock'), create a discrete subtask: 'Check and count pending orders'.\n"
            "- Do NOT fabricate payment, checkout, or billing steps unless the user explicitly requested payment or checkout.\n"
            "- When interacting with an item from a list or search results, generate steps to select/open the target item, then perform the requested action.\n"
            "- Conversational Feedback & Outcome Corrections:\n"
            "  * If user input corrects or reports a discrepancy in the outcome of an earlier turn, recognize it as an inspection and remediation goal. Do not extract slot values from negative statements or reproaches (e.g. statements reporting that an action failed, did not happen, or occurred unexpectedly).\n"
            f"{url_context}\n"
            f"{session_info}\n"
            f"User request: {task}\n\n"
            "Respond in valid JSON only:\n"
            "{\n"
            '  "intent": "<normalized 1-sentence goal>",\n'
            '  "slots": { ... },\n'
            '  "subtasks": ["step 1 description", "step 2 description", ...],\n'
            '  "missing_details": [],\n'
            '  "clarifying_question": null\n'
            "}"
        )
        cache_payload = {
            "task": task,
            "initial_url": initial_url,
            "session": session_context or {},
        }
        from laya_browser_agent.cache import global_prompt_cache
        cached_parsed = global_prompt_cache.get("task_planner", self.ollama_model, cache_payload)
        is_cache_hit = False
        parsed = None

        if cached_parsed and isinstance(cached_parsed, dict):
            parsed = cached_parsed
            is_cache_hit = True
            self.telemetry["cache_reads"] += 1
            self.telemetry["latency_saved_ms"] += 1800.0
        else:
            client = get_llm_client()
            messages = [
                {
                    "role": "system",
                    "content": "You are a parameter and plan organizer for web automation. Output valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ]
            parsed, in_tok, out_tok, elapsed_ms = client.chat_json(messages, timeout=25.0)
            if isinstance(parsed, dict):
                self.telemetry["input_tokens"] += in_tok
                self.telemetry["output_tokens"] += out_tok
                self.telemetry["cache_writes"] += 1
                global_prompt_cache.set("task_planner", self.ollama_model, cache_payload, parsed, latency_saved_ms=elapsed_ms)

        if isinstance(parsed, dict):
            intent = str(parsed.get("intent") or task)
            slots = parsed.get("slots", {})
            if not isinstance(slots, dict):
                slots = {}

            # Normalize slots
            # A. Prioritize deterministic credentials from the human request if found (prevents LLM censorship)
            heuristic = self._extract_via_heuristics(task, session_context=session_context)
            if "username" in heuristic.slots and "password" in heuristic.slots:
                slots["username"] = heuristic.slots["username"]
                slots["password"] = heuristic.slots["password"]
            else:
                if "username" in heuristic.slots and (
                    not slots.get("username")
                    or "@" in str(slots.get("username", ""))
                    or str(slots.get("username", "")).lower() != str(heuristic.slots["username"]).lower()
                ):
                    slots["username"] = heuristic.slots["username"]

                u_val = str(slots.get("username", ""))
                if "/" in u_val and "password" not in slots:
                    parts = u_val.split("/")
                    slots["username"] = parts[0].strip()
                    slots["password"] = parts[1].strip()

            # Sanitize explanatory placeholders from model output
            for k in list(slots.keys()):
                v_str = str(slots[k]).lower().strip()
                if (
                    "not specified" in v_str
                    or "not provided" in v_str
                    or "not given" in v_str
                    or v_str in ("none", "null", "n/a", "undefined", "unknown", "")
                    or v_str.startswith("<")
                ):
                    slots.pop(k, None)

            # B. Merge other missing or redacted slots from heuristics
            for hk, hv in heuristic.slots.items():
                val = str(slots.get(hk, ""))
                if not val or val.startswith("<") or "*" in val or "hidden" in val:
                    slots[hk] = hv

            # C. Ensure quantity is integer
            if "quantity" in slots:
                try:
                    slots["quantity"] = int(slots["quantity"])
                except (ValueError, TypeError):
                    slots["quantity"] = 1
            else:
                slots["quantity"] = 1

            # D. Filter out dropdown/status/generic terms from search/query slots
            dropdown_terms = ("status", "dropdown", "combobox", "out of stock", "in stock", "active", "low stock", "cancelled", "approved", "rejected", "pending", "all")
            for sk in ("search", "query"):
                val = str(slots.get(sk, "")).lower().strip()
                if val in dropdown_terms or any(dt == val for dt in ("products", "product", "items", "item", "catalog", "orders", "order")):
                    slots.pop(sk, None)

            # E. Clean and mirror product/search slots
            if "product" in slots and str(slots["product"]).lower().strip() not in ("products", "product", "items", "item"):
                clean_prod = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", str(slots["product"]), flags=re.IGNORECASE).strip()
                slots["product"] = clean_prod
                if not slots.get("search"):
                    slots["search"] = clean_prod
                if not slots.get("query"):
                    slots["query"] = clean_prod

            for sk in ("search", "query"):
                if sk in slots:
                    slots[sk] = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", str(slots[sk]), flags=re.IGNORECASE).strip()

            raw_subtasks = parsed.get("subtasks", [])
            flattened = []
            if isinstance(raw_subtasks, list):
                for s in raw_subtasks:
                    if isinstance(s, dict):
                        if "steps" in s and isinstance(s["steps"], list):
                            flattened.extend([str(step).strip() for step in s["steps"] if step])
                        elif "step" in s:
                            flattened.append(str(s["step"]).strip())
                        elif "action" in s:
                            flattened.append(str(s["action"]).strip())
                        elif "description" in s:
                            flattened.append(str(s["description"]).strip())
                        else:
                            for k, v in s.items():
                                if isinstance(v, list):
                                    flattened.extend([str(item).strip() for item in v if item])
                                elif isinstance(v, str) and len(v) > 3:
                                    flattened.append(v.strip())
                    elif isinstance(s, list):
                        flattened.extend([str(item).strip() for item in s if item])
                    elif s:
                        flattened.append(str(s).strip())

            clean_subtasks = self._consolidate_subtasks(flattened, task)

            raw_missing = parsed.get("missing_details", [])
            if isinstance(raw_missing, list):
                missing_details = [str(m).strip() for m in raw_missing if m]
            elif isinstance(raw_missing, str) and raw_missing.strip():
                missing_details = [raw_missing.strip()]
            else:
                missing_details = []

            clarifying_question = parsed.get("clarifying_question")
            clarifying_question = str(clarifying_question).strip() if clarifying_question else None

            low_task = task.lower()
            is_pure_logout = low_task.strip() in ("logout", "log out", "sign out")
            has_no_auth = any(phrase in low_task for phrase in ("no need to login", "without login", "skip login", "no login", "don't login", "do not login"))
            last_goal = (session_context.get("last_goal") or "") if session_context else ""
            prev_had_auth = any(w in last_goal.lower() for w in ("login", "log in", "signin", "sign in", "authenticate"))
            needs_auth = (
                not is_pure_logout
                and not has_no_auth
                and (
                    any(w in low_task for w in ("login", "log in", "signin", "sign in", "authenticate", "credentials", "credential"))
                    or (prev_had_auth and slots.get("username") and slots.get("password"))
                )
            )
            has_auth_step = any(any(w in s.lower() for w in ("login", "log in", "signin", "authenticate")) for s in clean_subtasks)
            if not has_auth_step and needs_auth and slots.get("username") and slots.get("password"):
                clean_subtasks.insert(0, f"Log in with credentials ({slots['username']})")

            if has_no_auth:
                clean_subtasks = [s for s in clean_subtasks if not any(w in s.lower() for w in ("log in with", "login with", "sign in with", "authenticate with"))]

            has_logout_step = any(any(w in s.lower() for w in ("logout", "log out", "sign out")) for s in clean_subtasks)
            if any(w in last_goal.lower() for w in ("logout", "log out", "sign out")) and not has_logout_step:
                clean_subtasks.append("Logout")

            # Reconcile missing details against extracted slots & context:
            # 1. Credentials
            if slots.get("username") and slots.get("password"):
                missing_details = [
                    m for m in missing_details
                    if not any(w in m.lower() for w in ("credential", "username", "password", "login details", "user details", "auth"))
                ]
            elif needs_auth:
                if not slots.get("username") and not slots.get("password"):
                    if not any("credential" in m.lower() or "username" in m.lower() for m in missing_details):
                        missing_details.append("login credentials (username and password)")
                elif slots.get("username") and not slots.get("password"):
                    if not any("password" in m.lower() for m in missing_details):
                        missing_details.append(f"password for user '{slots['username']}'")
                elif not slots.get("username") and slots.get("password"):
                    if not any("username" in m.lower() for m in missing_details):
                        missing_details.append("username")

            # 2. Filter / Dropdown
            if slots.get("status") or slots.get("option"):
                missing_details = [
                    m for m in missing_details
                    if not any(w in m.lower() for w in ("status", "dropdown", "option", "filter"))
                ]
            else:
                low_task = task.lower()
                if any(w in low_task for w in ("dropdown", "filter by status", "select status", "filter products")) and not any(k in slots for k in ("status", "option")):
                    if not any("status" in m.lower() or "option" in m.lower() for m in missing_details):
                        missing_details.append("status or dropdown option to select")

            # 3. Target URL: If initial_url or default URL exists or localhost
            if initial_url or "localhost" in task or "http" in task:
                missing_details = [
                    m for m in missing_details
                    if not any(w in m.lower() for w in ("url", "website", "site", "target"))
                ]

            # 4. Formulate clarifying question if missing details remain
            if missing_details:
                if not clarifying_question:
                    clarifying_question = f"Could you please specify: {', '.join(missing_details)}?"
            else:
                clarifying_question = None

            confident_horizon, confident_subtasks, deferred_goal = self.partition_confident_horizon(clean_subtasks, task)

            return StructuredPlan(
                intent=intent,
                slots=slots,
                subtasks=clean_subtasks,
                source=f"Local Prompt Cache ({self.ollama_model}, <0.2ms)" if is_cache_hit else f"Local LLM ({self.ollama_model})",
                missing_details=missing_details,
                clarifying_question=clarifying_question,
                confident_horizon=confident_horizon,
                confident_subtasks=confident_subtasks,
                deferred_goal=deferred_goal,
            )
    def _consolidate_subtasks(self, raw_subtasks: List[str], task: str) -> List[str]:
        """Consolidates fragmented subtasks, merges dropdown open+select pairs, and prunes redundant screenshots."""
        clean = [
            s for s in raw_subtasks
            if s and not any(w in s.lower() for w in ("initial url", "start url", "navigate to initial", "visit initial", "open initial"))
        ]
        if len(clean) > 1 and clean[0].lower().startswith(("go to http", "navigate to http", "visit http", "open http")):
            clean.pop(0)

        # Prune purely descriptive, passive, or observation clauses (that do NOT contain actionable verbs)
        action_verbs = ("click", "navigate", "go to", "press", "select", "find", "enter", "type", "open", "submit")
        clean = [
            s for s in clean
            if s and not (
                any(s.lower().strip().startswith(p) for p in ("there will be", "there are", "you will see"))
                or (
                    any(s.lower().strip().startswith(p) for p in ("if there are many pages", "if there are multiple pages"))
                    and not any(v in s.lower() for v in action_verbs)
                )
            )
        ]

        # Consolidate multi-page pagination instructions
        has_pagination_intent = any(any(w in s.lower() for w in ("pagination", "all pages", "each page", "every page", "different pages", "multiple pages")) for s in clean)
        if has_pagination_intent:
            p_merged = []
            p_added = False
            for s in clean:
                s_low = s.lower()
                is_p_step = (
                    any(w in s_low for w in ("pagination", "all pages", "each page", "every page", "different pages", "repeat step", "repeat until"))
                    or ("next" in s_low and any(w in s_low for w in ("button", "page", "click", "find", "navigate")))
                    or (p_added and "screenshot" in s_low)
                )
                if is_p_step:
                    if not p_added:
                        p_merged.append("Go to all pages via pagination and take screenshot of each page")
                        p_added = True
                else:
                    p_merged.append(s)
            clean = p_merged

        # Merge pairs and prune redundant subtasks
        merged = []
        skip = False
        for i in range(len(clean)):
            if skip:
                skip = False
                continue
            st_cur = clean[i].lower()
            if i + 1 < len(clean):
                st_nxt = clean[i + 1].lower()
                if any(w in st_cur for w in ("select", "choose", "pick", "find")) and any(w in st_nxt for w in ("order", "buy", "purchase", "checkout")):
                    merged.append(clean[i + 1])
                    skip = True
                    continue
                if any(w in st_cur for w in ("check if there are multiple pages", "check pagination", "check if multiple pages", "if there are many pages", "if there are multiple pages")):
                    if any(w in st_nxt for w in ("go to", "each page", "all pages", "pagination")):
                        merged.append(clean[i + 1])
                        skip = True
                        continue

                # Consolidate dropdown open + option selection
                is_dd_open = any(w in st_cur for w in ("dropdown", "combobox", "all status", "filter")) and any(w in st_cur for w in ("click", "open", "find", "press", "from that", "select", "choose"))
                is_dd_select = any(w in st_nxt for w in ("select", "choose", "pick", "set")) and any(w in st_nxt for w in ("dropdown", "combobox", "option", "status", "filter", "stock"))
                if is_dd_open and is_dd_select:
                    opt_m = re.search(r"['\"‘”]([^'\"’“”]+)['\"’“”]", clean[i + 1])
                    dd_m = re.search(r"['\"‘”]([^'\"’“”]+)['\"’“”]", clean[i])
                    if opt_m and dd_m:
                        merged.append(f"Select '{opt_m.group(1).strip()}' from '{dd_m.group(1).strip()}' dropdown")
                    else:
                        merged.append(clean[i + 1])
                    skip = True
                    continue

                # Prune standalone screenshot immediately preceding pagination screenshot loop
                is_sc_cur = any(w in st_cur for w in ("take screenshot", "take a screenshot", "capture screenshot", "save screenshot", "screenshot"))
                is_pagination_nxt = any(w in st_nxt for w in ("all pages", "pagination", "each page via pagination", "every page"))
                if is_sc_cur and is_pagination_nxt:
                    continue

                if any(w in st_cur for w in ("all pages", "pagination", "multiple pages")) and any(w in st_nxt for w in ("each page", "every page", "screenshot", "snapshot")):
                    merged.append(f"{clean[i]} and take screenshot of each page")
                    skip = True
                    continue
            merged.append(clean[i])
        clean = merged

        # Prune spurious search/inspect steps if task did not contain search intent
        has_search_intent = any(w in task.lower() for w in ("search", "find", "lookup", "query"))
        if not has_search_intent:
            clean = [
                s for s in clean
                if not (s.startswith("Enter '") and "into search input box" in s)
                and not s.startswith("Inspect search results and note the price of")
            ]

        if not any(w in task.lower() for w in ("checkout", "pay", "payment", "credit card", "billing")):
            clean = [s for s in clean if not any(w in s.lower() for w in ("checkout", "complete payment", "pay for", "enter payment", "proceed to payment"))]

        if any(w in task.lower() for w in ("logout", "log out", "sign out", "signout")):
            if not clean or not any(w in clean[-1].lower() for w in ("logout", "log out", "sign out", "signout")):
                clean.append("Logout")

        normalized = []
        for s in clean:
            s_low = s.lower()
            if any(w in s_low for w in ("generate report", "write report", "create report", "save report", "report with", "count in the report", "i want the count in the report")):
                continue
            # Filter out negative constraints / non-actionable instructions
            if any(w in s_low for w in (
                "no need to", "without login", "skip login", "no login", "do not login", "don't login",
                "do not close", "don't close", "keep open", "keep browser", "keep tab", "keep tabs",
                "have separate tab", "separate tab for each", "different tabs"
            )):
                continue
            # Convert human "Go to products page for X on Y" or "Search for X" into machine-actionable automation step
            m_srch = re.search(r"\b(?:go\s+to\s+products\s+page\s+(?:to\s+search\s+for|for)|search\s+for|find|lookup)\s+['\"]?([^'\"’“”]+)['\"]?", s, flags=re.IGNORECASE)
            if m_srch:
                raw_q = m_srch.group(1).strip()
                clean_q = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", raw_q, flags=re.IGNORECASE).strip()
                m_on = re.search(r"\s+(?:on|in|using|from)\s+([a-zA-Z0-9\._\-]+(?:\.[a-zA-Z]{2,})?)\s*$", clean_q, flags=re.IGNORECASE)
                site_domain = ""
                if m_on:
                    site_domain = m_on.group(1).strip()
                    clean_q = clean_q[:m_on.start()].strip()

                if clean_q and len(clean_q) > 2 and clean_q.lower() not in ("products", "product", "items", "item"):
                    if site_domain and "." in site_domain:
                        normalized.append(f"Go to {site_domain} and enter '{clean_q}' into search input box and submit")
                    else:
                        normalized.append(f"Enter '{clean_q}' into search input box and submit")
                    continue

            # If subtask is an abstract "Go to products page" but task contains a target search item
            if s_low.strip() in ("go to products page", "go to products", "products page", "navigate to products"):
                m_item = re.search(r"\b(?:for|find|search|price\s+of|buy|get)\s+([a-zA-Z0-9\s\-_]+?)(?:\s+(?:on|in|at|using|from)\b|$)", task, flags=re.IGNORECASE)
                if m_item:
                    q = m_item.group(1).strip()
                    clean_item = re.sub(r"^(?:and\s+)?(?:note|check|find)?\s*(?:the\s+)?(?:price|cost|rate)\s+of\s+", "", q, flags=re.IGNORECASE).strip()
                    if clean_item and len(clean_item) > 2 and clean_item.lower() not in ("products", "product", "items", "item"):
                        normalized.append(f"Enter '{clean_item}' into search input box and submit")
                        continue

            inspect_match = re.search(r"\b(?:check|count|verify|inspect)\s+(?:if\s+there\s+are\s+any\s+)?(.+)", s, re.IGNORECASE)
            if inspect_match and any(w in s_low for w in ("count", "how many", "pending", "status", "open", "failed", "active", "total")):
                normalized.append(f"Check and count {inspect_match.group(1).strip()}")
            else:
                normalized.append(s)
        return normalized or clean or [task]

    def _extract_via_heuristics(self, task: str, session_context: Optional[Dict[str, Any]] = None) -> StructuredPlan:
        params: Dict[str, Any] = {}

        # 0. Inherit known slots from previous conversation turns
        if session_context:
            known_slots = session_context.get("slots", {}) if "slots" in session_context else session_context
            for sk in ("username", "password", "target_url", "orderId", "status", "quantity"):
                if sk in known_slots and known_slots[sk] and sk not in params:
                    params[sk] = known_slots[sk]

        # 1. Key=value or key: value pairs (exclude URLs / URL schemes)
        for match in re.finditer(r"\b([a-zA-Z0-9_\-]+)\s*[:=]\s*[\"']?([^\"',\s\)]+)[\"']?", task):
            k, v = match.group(1).lower(), match.group(2).strip().strip("\"'")
            if k in ("http", "https", "ftp") or v.startswith("//"):
                continue
            params[k] = int(v) if v.isdigit() else v

        # 2. Email addresses
        email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", task)
        if email_match:
            params["email"] = email_match.group(0)

        # 3. Credentials pattern: e.g. "as user (user / User@123)" or "admin / Admin@123"
        # First strip URLs from the text so domain slashes (e.g. "domain.com/path") are never treated as credentials
        task_no_urls = re.sub(r"https?://\S+", "", task)
        cred_matches = re.findall(r"\b([a-zA-Z0-9_\-]+)\s*\/\s*([^\s,\)\.]+)", task_no_urls)
        STOP_WORDS = {"and", "or", "the", "in", "to", "for", "on", "at", "with", "is", "it", "a", "an", "all", "each", "my"}
        valid_creds = [
            (u.strip(), p.strip()) for u, p in cred_matches
            if u.strip().lower() not in STOP_WORDS and p.strip().lower() not in STOP_WORDS
        ]
        if valid_creds:
            user_pair = valid_creds[0]
            params["username"] = user_pair[0]
            params["password"] = user_pair[1]

        # 3b. Explicit persona mention without inline password: e.g. "login as admin"
        m_user = re.search(r"\b(?:login|signin|sign in|log in)\s+as\s+([a-zA-Z0-9_\-]+)", task, flags=re.IGNORECASE)
        if m_user:
            new_u = m_user.group(1).strip()
            prev_u = (session_context.get("slots", {}) if session_context else {}).get("username")
            if prev_u and prev_u.lower() != new_u.lower():
                # Persona changed across session turns: invalidate previous session password
                params.pop("password", None)
            params["username"] = new_u

        # 4. Status and dropdown options (e.g. "status as 'out of stock'", "status: pending", "dropdown with 'all status'")
        status_match = re.search(r"\b(?:status\s+(?:as|to|of|is)\s+|status\s*[:=]\s*|option\s*[:=]?\s*)[\"']?([^\"',\.;]+)[\"']?|status\s+[\"']([^\"']+)[\"']", task, re.IGNORECASE)
        if status_match:
            st_val = (status_match.group(1) or status_match.group(2) or "").strip()
            if st_val.lower() not in ("page", "products", "orders", "all", "the", "a", "and", "then", "logout", "log out") and not st_val.lower().startswith("and "):
                params["status"] = st_val
                params["option"] = st_val

        dropdown_match = re.search(r"\b(?:dropdown\s+(?:with|named|for|called)?|combobox\s+(?:with|named)?|click\s+one\s+with)\s*[\"']?([^\"',\.;]+)[\"']?", task, re.IGNORECASE)
        if dropdown_match:
            dd_val = dropdown_match.group(1).strip()
            if dd_val.lower() not in ("page", "products", "orders"):
                params["dropdown"] = dd_val

        # 4b. Common Form Fields (Name, Phone, Message, Subject, Address, City, Zip)
        if "name" not in params:
            name_m = re.search(r"\bname\s+(?:is\s+|as\s+|to\s+)?['\"]([^'\"]+)['\"]", task, re.IGNORECASE)
            if name_m:
                params["name"] = name_m.group(1).strip()

        if "phone" not in params:
            phone_m = re.search(r"\b(?:phone|mobile|tel)\s*[:=]?\s*['\"]?([0-9\+\-\s\(\)]{7,15})['\"]?", task, re.IGNORECASE)
            if phone_m:
                params["phone"] = phone_m.group(1).strip()

        if "message" not in params:
            msg_m = re.search(r"\b(?:message|comment|feedback|notes?|body)\s+(?:is\s+|as\s+|to\s+)?['\"]([^'\"]+)['\"]", task, re.IGNORECASE)
            if msg_m:
                params["message"] = msg_m.group(1).strip()

        if "subject" not in params:
            subj_m = re.search(r"\b(?:subject|title)\s+(?:is\s+|as\s+|to\s+)?['\"]([^'\"]+)['\"]", task, re.IGNORECASE)
            if subj_m:
                params["subject"] = subj_m.group(1).strip()

        if "address" not in params:
            addr_m = re.search(r"\b(?:address|street)\s+(?:is\s+|as\s+|to\s+)?['\"]([^'\"]+)['\"]", task, re.IGNORECASE)
            if addr_m:
                params["address"] = addr_m.group(1).strip()

        if "city" not in params:
            city_m = re.search(r"\bcity\s+(?:is\s+|as\s+|to\s+)?['\"]([^'\"]+)['\"]", task, re.IGNORECASE)
            if city_m:
                params["city"] = city_m.group(1).strip()

        if "zip" not in params:
            zip_m = re.search(r"\b(?:zip|postal|pincode|postcode)\s*[:=]?\s*['\"]?([0-9a-zA-Z\-]{3,10})['\"]?", task, re.IGNORECASE)
            if zip_m:
                params["zip"] = zip_m.group(1).strip()

        # 5. Explicit search queries (require search/find/query/lookup verbs)
        search_match = re.search(r"\b(?:search|find|lookup|query)\s+(?:for\s+|in\s+|by\s+)?[\"']?([a-zA-Z0-9_\-]+)[\"']?", task, re.IGNORECASE)
        if search_match and "search" not in params:
            kw = search_match.group(1).strip()
            ignored_kw = (
                "products", "product", "items", "item", "catalog", "goods", "orders", "order",
                "all", "each", "page", "pages", "status", "dropdown", "the", "a", "an", "this",
                "that", "one", "button", "link", "icon", "tab", "menu", "next", "prev", "previous",
            )
            if kw.lower() not in ignored_kw:
                params["search"] = kw
                params["query"] = kw
        elif "search" not in params and "status" not in params:
            quoted = [q.strip() for q in re.findall(r"[\"']([^\"']+)[\"']", task)]
            if quoted:
                first_q = quoted[0]
                pwd_val = str(params.get("password", ""))
                if first_q != pwd_val and not any(w in first_q.lower() for w in ("status", "dropdown", "select", "all", "out of stock", "active", "pending", "approved")):
                    params["query"] = first_q
                    params["search"] = first_q

        # 6. Quantities: match explicit unit words or "buy/order N" (prevent matching numbers inside passwords)
        qty_match = re.search(r"\b(\d+)\s+(?:items?|units?|pieces?|tickets?|qty)\b", task, re.IGNORECASE)
        if not qty_match:
            qty_match = re.search(r"\b(?:buy|order|purchase|get|add|count)\s*[:=]?\s*(\d+)\b", task, re.IGNORECASE)
        if qty_match:
            try:
                params["quantity"] = int(qty_match.group(1))
            except ValueError:
                params["quantity"] = 1
        else:
            params["quantity"] = 1

        # 7. Entity IDs and prefixes (e.g., "order starting with 2e47d384", "order 360f30c4", "orderId: abc")
        id_match = re.search(r"\b(?:order|product|item|user|category)\s+(?:starting with|id|#|number|with id)?\s*([a-fA-F0-9\-]{6,36})\b", task, re.IGNORECASE)
        if id_match:
            entity_val = id_match.group(1).strip()
            params["orderId"] = entity_val
            params["order_id"] = entity_val
            params["id"] = entity_val

        # 8. Multi-turn grounding: inherit previous outcome entities and open tabs
        open_tabs = (session_context or {}).get("open_tabs") or []
        last_outcome = (session_context or {}).get("last_outcome") or {}
        if not open_tabs and last_outcome:
            open_tabs = last_outcome.get("open_tabs") or []
        prior_slots = (session_context or {}).get("slots") or {}
        if not prior_slots and last_outcome:
            prior_slots = last_outcome.get("extracted_slots") or {}
        for sk in ("search", "query", "variant"):
            if sk in prior_slots and sk not in params and prior_slots[sk]:
                params[sk] = prior_slots[sk]

        low_t = task.lower()
        has_multi_site = any(w in low_t for w in ("each site", "both sites", "each website", "both websites", "all sites", "separate tab"))
        distinct_domains = []
        for t in open_tabs:
            h = urlparse(t.get("url", "")).netloc.lower().replace("www.", "")
            if h and h not in distinct_domains:
                distinct_domains.append(h)

        # Split subtasks by clause delimiters: commas, 'and', 'then', 'after that', periods
        clauses = re.split(r",\s*(?:and\s+)?|\s+and\s+|\s+then\s+|\s+after that\s+|\.\s+", task, flags=re.IGNORECASE)
        # Filter out conversational modifiers, pleasantries, and tab modality phrases
        action_clauses = [
            re.sub(r"^(and|then|also|please|can you|could you)\s+", "", c.strip(), flags=re.IGNORECASE).strip()
            for c in clauses
            if not any(w in c.lower() for w in (
                "separate tab", "each site", "both sites", "each website", "both websites", "all sites",
                "do not close", "dont close", "keep open", "keep browser", "keep tab", "keep tabs",
                "okay", "ok", "great", "cool", "thanks", "thank you", "hello", "hi", "in different tabs"
            )) and len(c.strip()) > 2
        ]

        if has_multi_site and len(distinct_domains) >= 2 and action_clauses:
            target_entity = params.get("search") or params.get("query") or ""
            subtasks = []
            for d in distinct_domains:
                subtasks.append(f"Switch to {d} tab")
                for act in action_clauses:
                    resolved_act = act
                    if target_entity:
                        resolved_act = re.sub(r"\b(?:it|them|that|the item|the product)\b", f"'{target_entity}'", resolved_act, flags=re.IGNORECASE)
                    subtasks.append(resolved_act)
        else:
            subtasks = []
            for c in clauses:
                raw = re.sub(r"^(and|then|also|please)\s+", "", c.strip(), flags=re.IGNORECASE).strip()
                if not raw or len(raw) <= 2:
                    continue
                # Prune trailing/standalone report-only sentences (reporting is performed automatically by agent)
                if any(w in raw.lower() for w in ("i want the count in the report", "count in the report", "put the count in the report", "in the report")):
                    continue
                count_match = re.search(r"\b(?:check|count|verify|confirm)\s+(?:if\s+there\s+are\s+any\s+)?(.+)", raw, re.IGNORECASE)
                if count_match and any(w in raw.lower() for w in ("count", "how many", "pending", "status", "open", "failed", "active")):
                    subtasks.append(f"Check and count {count_match.group(1).strip()}")
                else:
                    subtasks.append(raw)
            if not subtasks:
                subtasks = [task]
        subtasks = self._consolidate_subtasks(subtasks, task)

        # Context-aware follow-up subtask expansion:
        # If task targets an authenticated resource (orders, dashboard, profile, etc.) and credentials exist,
        # ensure login step is present:
        has_auth_step = any(any(w in s.lower() for w in ("login", "log in", "signin", "authenticate")) for s in subtasks)
        low = task.lower()
        is_pure_logout = low.strip() in ("logout", "log out", "sign out")
        has_no_auth = any(phrase in low for phrase in ("no need to login", "without login", "skip login", "no login", "don't login", "do not login"))
        last_goal = (session_context.get("last_goal") or "") if session_context else ""
        prev_had_auth = any(w in last_goal.lower() for w in ("login", "log in", "signin", "sign in", "authenticate"))
        needs_auth = (
            not is_pure_logout
            and not has_no_auth
            and (
                any(w in low for w in ("login", "log in", "signin", "sign in", "authenticate", "credential", "credentials"))
                or (prev_had_auth and params.get("username") and params.get("password"))
            )
        )
        if not has_auth_step and needs_auth and params.get("username") and params.get("password"):
            subtasks.insert(0, f"Log in with credentials ({params['username']})")

        # If previous goal had logout and current request doesn't explicitly mention not logging out:
        last_goal = (session_context.get("last_goal") or "") if session_context else ""
        has_logout_step = any(any(w in s.lower() for w in ("logout", "log out", "sign out")) for s in subtasks)
        if any(w in last_goal.lower() for w in ("logout", "log out", "sign out")) and not has_logout_step:
            subtasks.append("Logout")

        missing: List[str] = []
        if needs_auth:
            if not params.get("username") and not params.get("password"):
                missing.append("login credentials (username and password)")
            elif params.get("username") and not params.get("password"):
                missing.append(f"password for user '{params['username']}'")
            elif not params.get("username") and params.get("password"):
                missing.append("username")
        if any(w in low for w in ("filter by status", "select status", "filter products")) and not params.get("status") and not params.get("option"):
            missing.append("status or dropdown option to select")

        clarify = f"Could you please specify {', '.join(missing)}?" if missing else None

        confident_horizon, confident_subtasks, deferred_goal = self.partition_confident_horizon(subtasks, task)

        return StructuredPlan(
            intent=task.strip(),
            slots=params,
            subtasks=subtasks,
            source="Instant Heuristics",
            missing_details=missing,
            clarifying_question=clarify,
            confident_horizon=confident_horizon,
            confident_subtasks=confident_subtasks,
            deferred_goal=deferred_goal,
        )

    def partition_confident_horizon(self, subtasks: List[str], task: str) -> Tuple[int, List[str], Optional[str]]:
        """
        Partitions subtasks into an initial confident horizon and a deferred intent.
        Confident horizon includes definite initial actions (e.g. navigation, login, initial search query).
        Subtasks that depend on live screen inspection (speculative dropdowns, pagination, audits)
        are deferred until the agent reaches and settles on the target screen.
        """
        if not subtasks:
            return 0, [], None

        uncertain_idx = None
        for idx, st in enumerate(subtasks):
            if self._is_uncertain_or_speculative_subtask(st):
                uncertain_idx = idx
                break

        if uncertain_idx is None:
            # All subtasks are confident upfront
            return len(subtasks), list(subtasks), None

        # Confident horizon covers steps before the first uncertain subtask
        # Preserve at least 1 anchor step if possible
        horizon_count = max(1, uncertain_idx)
        confident = subtasks[:horizon_count]
        deferred_subtasks = subtasks[horizon_count:]
        deferred_text = "; ".join(deferred_subtasks)

        return horizon_count, confident, deferred_text

    @staticmethod
    def _is_uncertain_or_speculative_subtask(subtask: str) -> bool:
        s = subtask.lower().strip()
        # 1. Explicit conditional clauses
        if any(s.startswith(p) or f" {p} " in f" {s} " for p in (
            "if ", "if there are", "if multiple", "in case", "whenever", "wherever", "should there be"
        )):
            return True

        # 2. Speculative UI widget interactions
        if any(w in s for w in ("dropdown", "combobox", "select status", "filter by", "click one with", "from that click", "select option")):
            return True

        # 3. Multi-page pagination loops
        if any(w in s for w in ("pagination", "all pages", "each page", "every page", "next page", "next button", "different pages")):
            return True

        # 4. Data inspection / audits on unseen result sets
        if any(w in s for w in ("check and count", "how many", "check if there are any", "inspect count")):
            return True

        # 5. Batch mutations on unseen entity lists
        if any(w in s for w in ("cancel all", "delete all", "remove all", "approve all", "reject all", "all open", "all pending")):
            return True

        return False

    def expand_plan_from_screen(
        self,
        goal: str,
        deferred_goal: str,
        completed_subtasks: List[str],
        current_url: str,
        screen_elements: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        extracted_slots: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Dynamically synthesizes grounded subtasks to fulfill the deferred user intent."""
        return expand_plan_from_screen(
            extractor=self,
            goal=goal,
            deferred_goal=deferred_goal,
            completed_subtasks=completed_subtasks,
            current_url=current_url,
            screen_elements=screen_elements,
            tools=tools,
            extracted_slots=extracted_slots,
        )

    def remediate_plan_from_screen(
        self,
        goal: str,
        failed_subtask: str,
        failure_reason: str,
        current_url: str,
        page_title: str,
        screen_elements: List[Dict[str, Any]],
        remaining_subtasks: List[str],
    ) -> Optional[List[str]]:
        """Adapts or alters the sequential plan when a subtask fails assertion or stagnates."""
        return remediate_plan_from_screen(
            extractor=self,
            goal=goal,
            failed_subtask=failed_subtask,
            failure_reason=failure_reason,
            current_url=current_url,
            page_title=page_title,
            screen_elements=screen_elements,
            remaining_subtasks=remaining_subtasks,
        )

    def _expand_via_heuristics(self, *args, **kwargs):
        from .expansion import _expand_via_heuristics
        return _expand_via_heuristics(*args, **kwargs)

    def _remediate_via_heuristics(self, *args, **kwargs):
        from .expansion import _remediate_via_heuristics
        return _remediate_via_heuristics(*args, **kwargs)

