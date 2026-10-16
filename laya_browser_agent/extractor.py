"""Universal parameter and entity extractor combining local Ollama with heuristic fallback."""

import json
import re
from typing import Any, Dict
import httpx


class SlotExtractor:
    """Extracts entity parameters, credentials, numbers, and keywords from user prompt for any web task."""

    def __init__(self, ollama_url: str = "http://localhost:11434", ollama_model: str = "llama3.1:8b"):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model

    def extract(self, task: str) -> Dict[str, Any]:
        """Extracts parameters using local Ollama if running, otherwise uses deterministic heuristics."""
        # 1. Try local Ollama first for deep semantic extraction across any web domain
        try:
            params = self._extract_via_ollama(task)
            if params and isinstance(params, dict):
                return params
        except Exception:
            pass

        # 2. Universal deterministic heuristic fallback (cost: $0, 0ms latency)
        return self._extract_via_heuristics(task)

    def _extract_via_ollama(self, task: str) -> Dict[str, Any]:
        prompt = (
            "Analyze this web automation request and extract all relevant parameters, inputs, "
            "credentials, numbers, keywords, filters, and entities into a flat JSON object.\n"
            f"User request: {task}"
        )
        with httpx.Client(timeout=4.0) as client:
            resp = client.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a parameter extractor for a browser automation agent. Output only valid JSON with extracted fields.",
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
                    return parsed
        return {}

    def _extract_via_heuristics(self, task: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        # 1. Explicit key=value or key: value pairs (e.g. email: user@test.com, code: 1234)
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

        # 4. Quoted terms (e.g. search "wireless mouse", title "Bug in login")
        quoted = re.findall(r"[\"']([^\"']+)[\"']", task)
        if quoted:
            params["query"] = quoted[0]
            params["search"] = quoted[0]

        # 5. Search queries without quotes (e.g. "search for electronics", "find hotels")
        search_match = re.search(r"\b(?:search|find|filter|query)(?:\s+(?:for|in|by))?\s+([a-zA-Z0-9_\-]+)", task, re.IGNORECASE)
        if search_match and "search" not in params:
            params["search"] = search_match.group(1).strip()
            params["query"] = search_match.group(1).strip()

        # 6. Quantities / counts (e.g. "buy 2 items", "5 units", "count: 10")
        qty_match = re.search(r"\b(\d+)\s*(?:items?|units?|pieces?|tickets?|qty)?\b", task, re.IGNORECASE)
        if qty_match and "quantity" not in params:
            try:
                params["quantity"] = int(qty_match.group(1))
            except ValueError:
                params["quantity"] = 1

        return params
