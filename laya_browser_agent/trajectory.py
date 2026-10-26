"""Trajectory Logger for Laya Browser Agent.

Records step-by-step execution traces into $LAYA_HOME/trajectories/<run_id>.jsonl
(default ~/.laya/trajectories)
for offline evaluation, post-run debugging, regression test suites, and
fine-tuning dataset generation (DAgger).
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Words that mark a dict key as secret. Keys are split into word tokens (snake/kebab/camelCase)
# and matched per token, so 'api_token' and 'authToken' are masked but 'author' is not.
_SECRET_WORDS = frozenset({
    "password", "passwd", "secret", "token", "apikey", "credential", "credentials", "passcode",
})
_KEY_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _is_secret_key(key: Any) -> bool:
    words = [w.lower() for w in _KEY_TOKEN_RE.findall(str(key))]
    if any(w in _SECRET_WORDS for w in words):
        return True
    return any(a == "api" and b == "key" for a, b in zip(words, words[1:])) or \
        any(a == "private" and b == "key" for a, b in zip(words, words[1:]))


# Pattern: word/Password123 or user/User@123 style inline credentials
# Also catches password='...', password: ... forms
_INLINE_CRED_RE = re.compile(
    r"(\w[\w.+-]*)"          # username / label
    r"/"                     # separator
    r"([A-Za-z0-9@#!$%^&*_+-]{6,64})",  # password token
)


def mask_sensitive_values(obj: Any) -> Any:
    """Recursively masks passwords and secret credentials in dicts, lists, and strings.

    Masking rules:
    - Dict key whose word tokens include a secret word (see _SECRET_WORDS): mask value.
    - Strings: replace user/Password-style inline credentials.
    """
    if isinstance(obj, dict):
        masked = {}
        for k, v in obj.items():
            if _is_secret_key(k):
                masked[k] = "••••••••"
            else:
                masked[k] = mask_sensitive_values(v)
        return masked
    elif isinstance(obj, list):
        return [mask_sensitive_values(item) for item in obj]
    elif isinstance(obj, str):
        return _INLINE_CRED_RE.sub(lambda m: f"{m.group(1)}/••••••••", obj)
    return obj


class TrajectoryLogger:
    """Appends execution steps as JSON lines into a run-specific trajectory log."""

    def __init__(self, run_id: Optional[str] = None, base_dir: Optional[str] = None):
        self.run_id = run_id or f"run_{int(time.time())}_{os.getpid()}"
        base = Path(base_dir) if base_dir else (Path(os.environ.get("LAYA_HOME") or (Path.home() / ".laya")) / "trajectories")
        base.mkdir(parents=True, exist_ok=True)
        self.log_path = base / f"{self.run_id}.jsonl"

    def log_step(
        self,
        step: int,
        subtask: str,
        goal: str,
        url: str,
        candidates: Dict[str, Any],
        chosen_action: str,
        confidence: float,
        action_params: Dict[str, Any],
        duration_ms: float,
        engine_info: str,
        llm_fallback: bool = False,
        screen_summary: Optional[List[Dict[str, Any]]] = None,
        questions: Optional[Dict[str, Any]] = None,
        laya_probabilities: Optional[Dict[str, float]] = None,
        post_condition_result: Optional[Any] = None,
        verified: Optional[bool] = None,  # None = unknown until Phase 1 post-condition check
    ) -> None:
        """Appends a sanitized step record to the trajectory JSONL file."""
        entry = {
            "run_id": self.run_id,
            "timestamp": time.time(),
            "step": step,
            # Mask goal inline to strip user/Password patterns
            "goal": mask_sensitive_values(goal),
            "subtask": mask_sensitive_values(subtask),
            "url": url,
            "candidates": mask_sensitive_values(candidates),
            "chosen_action": chosen_action,
            "confidence": round(float(confidence), 4),
            "action_params": mask_sensitive_values(action_params),
            "duration_ms": round(float(duration_ms), 2),
            "engine_info": engine_info,
            "llm_fallback": bool(llm_fallback),
            "screen_summary": mask_sensitive_values(screen_summary or []),
            "questions": questions or {},
            "laya_probabilities": laya_probabilities or {},
            "post_condition_result": mask_sensitive_values(post_condition_result),
            # None = "unknown"; True/False set only when Phase 1 verifies post-conditions.
            "verified": verified,
        }

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def get_entries(self) -> List[Dict[str, Any]]:
        """Reads all entries logged for this run."""
        if not self.log_path.exists():
            return []
        entries = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception:
            pass
        return entries
