"""Zero-cost slot and parameter extractor combining local Ollama with regex fallback."""

import json
import re
from typing import Any, Dict
import httpx


class SlotExtractor:
    """Extracts entity parameters (credentials, quantities, keywords) from user prompt."""

    def __init__(self, ollama_url: str = "http://localhost:11434", ollama_model: str = "llama3.1:8b"):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model

    def extract(self, task: str) -> Dict[str, Any]:
        """Extracts parameters using local Ollama if running, otherwise uses regex heuristics."""
        # 1. Try local Ollama first for deep semantic extraction (cost: $0)
        try:
            params = self._extract_via_ollama(task)
            if params:
                return params
        except Exception:
            pass

        # 2. Deterministic regex fallback (cost: $0, 0ms latency)
        return self._extract_via_regex(task)

    def _extract_via_ollama(self, task: str) -> Dict[str, Any]:
        prompt = (
            "Extract entity parameters from this user request into JSON format: "
            "username, password, quantity (integer), productId, searchKeyword, role. "
            f"User request: {task}"
        )
        with httpx.Client(timeout=4.0) as client:
            resp = client.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": [
                        {"role": "system", "content": "You are a parameter extractor. Output only valid JSON with extracted fields."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": "json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("message", {}).get("content", "{}")
                return json.loads(content)
        return {}

    def _extract_via_regex(self, task: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}

        # Credentials pattern: e.g. "as user (user / User@123)" or "admin / Admin@123"
        cred_matches = re.findall(r"([a-zA-Z0-9_\-]+)\s*\/\s*([^\s,\)\.]+)", task)
        if cred_matches:
            # First match or role based
            if any("admin" in c[0].lower() for c in cred_matches):
                admin_pair = next(c for c in cred_matches if "admin" in c[0].lower())
                params["admin_username"] = admin_pair[0].strip()
                params["admin_password"] = admin_pair[1].strip()
            user_pair = next((c for c in cred_matches if "admin" not in c[0].lower()), cred_matches[0])
            params["username"] = user_pair[0].strip()
            params["password"] = user_pair[1].strip()

        # Quantity pattern: e.g. "1 unit", "order 2 items"
        qty_match = re.search(r"\b(\d+)\s*(?:unit|units|item|items|piece|pieces|qty)?\b", task, re.IGNORECASE)
        if qty_match:
            try:
                params["quantity"] = int(qty_match.group(1))
            except ValueError:
                params["quantity"] = 1
        else:
            params["quantity"] = 1

        # Search query pattern
        search_match = re.search(r"search (?:for|the catalog for|products for)?\s*([a-zA-Z0-9_\-]+)", task, re.IGNORECASE)
        if search_match:
            params["search"] = search_match.group(1).strip()

        return params
