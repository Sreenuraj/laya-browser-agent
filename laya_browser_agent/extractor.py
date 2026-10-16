"""Universal parameter and subtask planner combining local Ollama with heuristic fallback."""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import httpx


@dataclass
class StructuredPlan:
    """Organized input representation produced from the user's raw prompt."""
    intent: str
    slots: Dict[str, Any]
    subtasks: List[str]
    source: str = "Local LLM"


class SlotExtractor:
    """Structures human requests into parameters and sequential subtasks for the decision model."""

    def __init__(self, ollama_url: str = "http://localhost:11434", ollama_model: str = "llama3.1:8b"):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model

    def extract(self, task: str, initial_url: str = "") -> StructuredPlan:
        """Structures user input using local Ollama if running, otherwise uses instant heuristics."""
        clean_task = (task or "").replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')

        # 1. Try local Ollama (runs in ~1s if warm) to organize the plan
        try:
            plan = self._extract_via_ollama(clean_task, initial_url=initial_url)
            if plan and plan.subtasks:
                return plan
        except Exception:
            pass

        # 2. Deterministic 0ms heuristic fallback
        return self._extract_via_heuristics(clean_task)

    def _extract_via_ollama(self, task: str, initial_url: str = "") -> Optional[StructuredPlan]:
        url_context = f"\nBrowser initial URL: {initial_url}" if initial_url else ""
        prompt = (
            "You are an autonomous web agent task planner.\n"
            "Analyze this human user request and return a clean, organized, structured execution plan in JSON:\n"
            "1. 'intent': concise 1-sentence normalized objective summary.\n"
            "2. 'slots': dictionary of extracted parameters (separate 'username', 'password', 'quantity' as int, 'search' or 'query' ONLY for text search keywords, 'status' or 'option' for dropdown / status selections, 'dropdown' for target dropdown names). Do NOT put dropdown or status filter values into search/query.\n"
            "3. 'subtasks': ordered list of concise sequential action steps to achieve the goal. Do NOT include opening or navigating to the initial URL (the browser is already open at that page). If login credentials are provided in the task, include 'Log in with credentials' as the very first step before performing actions. Keep subtasks strictly faithful to discrete goals without fabricating artificial sub-steps: do NOT split selecting/picking a product and buying/ordering it into two steps (combine into 'Place order for product'); separate taking a screenshot or logging out into their own individual steps.\n"
            f"{url_context}\n"
            f"User request: {task}\n\n"
            "Respond in valid JSON only:\n"
            "{\n"
            '  "intent": "<normalized 1-sentence goal>",\n'
            '  "slots": { ... },\n'
            '  "subtasks": ["step 1 description", "step 2 description", ...]\n'
            "}"
        )
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a parameter and plan organizer for web automation. Output valid JSON only.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": "json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("message", {}).get("content", "{}")
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    intent = str(parsed.get("intent") or task)
                    slots = parsed.get("slots", {})
                    if not isinstance(slots, dict):
                        slots = {}

                    # Normalize slots
                    # A. Prioritize deterministic credentials from the human request if found (prevents LLM censorship)
                    heuristic = self._extract_via_heuristics(task)
                    if "username" in heuristic.slots and "password" in heuristic.slots:
                        slots["username"] = heuristic.slots["username"]
                        slots["password"] = heuristic.slots["password"]
                    else:
                        u_val = str(slots.get("username", ""))
                        if "/" in u_val and "password" not in slots:
                            parts = u_val.split("/")
                            slots["username"] = parts[0].strip()
                            slots["password"] = parts[1].strip()

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
                    dropdown_terms = ("status", "dropdown", "combobox", "out of stock", "in stock", "active", "low stock", "cancelled", "approved", "rejected", "pending", "products", "product", "items", "item", "catalog", "goods", "orders", "order", "all")
                    for sk in ("search", "query"):
                        val = str(slots.get(sk, "")).lower()
                        if any(dt in val for dt in dropdown_terms):
                            slots.pop(sk, None)

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

                    clean_subtasks = [
                        s for s in flattened
                        if s and not any(w in s.lower() for w in ("initial url", "start url", "navigate to initial", "visit initial", "open initial"))
                    ]

                    # Merge pairs:
                    # 1. "select product" + "order product"
                    # 2. "check pagination / multiple pages" + "go to all pages / take screenshot"
                    merged = []
                    skip = False
                    for i in range(len(clean_subtasks)):
                        if skip:
                            skip = False
                            continue
                        st_cur = clean_subtasks[i].lower()
                        if i + 1 < len(clean_subtasks):
                            st_nxt = clean_subtasks[i + 1].lower()
                            if any(w in st_cur for w in ("select", "choose", "pick", "find")) and any(w in st_nxt for w in ("order", "buy", "purchase", "checkout")):
                                merged.append(clean_subtasks[i + 1])
                                skip = True
                                continue
                            if any(w in st_cur for w in ("check if there are multiple pages", "check pagination", "check if multiple pages")):
                                if any(w in st_nxt for w in ("go to", "each page", "all pages")):
                                    merged.append(clean_subtasks[i + 1])
                                    skip = True
                                    continue
                        merged.append(clean_subtasks[i])
                    clean_subtasks = merged or clean_subtasks

                    if not clean_subtasks:
                        clean_subtasks = [task]
                    return StructuredPlan(
                        intent=intent,
                        slots=slots,
                        subtasks=clean_subtasks,
                        source=f"Local LLM ({self.ollama_model})",
                    )
        return None

    def _extract_via_heuristics(self, task: str) -> StructuredPlan:
        params: Dict[str, Any] = {}

        # 1. Key=value or key: value pairs
        for match in re.finditer(r"\b([a-zA-Z0-9_\-]+)\s*[:=]\s*[\"']?([^\"',\s\)]+)[\"']?", task):
            k, v = match.group(1).lower(), match.group(2).strip()
            params[k] = int(v) if v.isdigit() else v

        # 2. Email addresses
        email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", task)
        if email_match:
            params["email"] = email_match.group(0)

        # 3. Credentials pattern: e.g. "as user (user / User@123)" or "admin / Admin@123"
        cred_matches = re.findall(r"([a-zA-Z0-9_\-]+)\s*\/\s*([^\s,\)\.]+)", task)
        if cred_matches and "username" not in params:
            user_pair = cred_matches[0]
            params["username"] = user_pair[0].strip()
            params["password"] = user_pair[1].strip()

        # 4. Status and dropdown options (e.g. "status as 'out of stock'", "dropdown with 'all status'")
        status_match = re.search(r"\b(?:status\s+(?:as|to|of)?|status\s*[:=]|option\s*[:=]?)\s*[\"']?([^\"',\.;]+)[\"']?", task, re.IGNORECASE)
        if status_match:
            st_val = status_match.group(1).strip()
            if st_val.lower() not in ("page", "products", "orders", "all", "the", "a"):
                params["status"] = st_val
                params["option"] = st_val

        dropdown_match = re.search(r"\b(?:dropdown\s+(?:with|named|for|called)?|combobox\s+(?:with|named)?|click\s+one\s+with)\s*[\"']?([^\"',\.;]+)[\"']?", task, re.IGNORECASE)
        if dropdown_match:
            dd_val = dropdown_match.group(1).strip()
            if dd_val.lower() not in ("page", "products", "orders"):
                params["dropdown"] = dd_val

        # 5. Explicit search queries (require search/find/query/lookup verbs)
        search_match = re.search(r"\b(?:search|find|lookup|query)\s+(?:for\s+|in\s+|by\s+)?[\"']?([a-zA-Z0-9_\-]+)[\"']?", task, re.IGNORECASE)
        if search_match and "search" not in params:
            kw = search_match.group(1).strip()
            if kw.lower() not in ("products", "product", "items", "item", "catalog", "goods", "orders", "order", "all", "each", "page", "pages", "status", "dropdown"):
                params["search"] = kw
                params["query"] = kw
        elif "search" not in params and "status" not in params:
            quoted = re.findall(r"[\"']([^\"']+)[\"']", task)
            if quoted:
                first_q = quoted[0].strip()
                if not any(w in first_q.lower() for w in ("status", "dropdown", "select", "all", "out of stock", "active", "pending", "approved")):
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

        # 8. Split subtasks by clause delimiters: commas, 'and', 'then', 'after that'
        clauses = re.split(r",\s*(?:and\s+)?|\s+and\s+|\s+then\s+|\s+after that\s+", task, flags=re.IGNORECASE)
        subtasks = [
            re.sub(r"^(and|then|also|please)\s+", "", c.strip(), flags=re.IGNORECASE).strip()
            for c in clauses if c.strip() and len(c.strip()) > 2
        ]
        if not subtasks:
            subtasks = [task]

        return StructuredPlan(
            intent=task.strip(),
            slots=params,
            subtasks=subtasks,
            source="Instant Heuristics",
        )
