"""Conversational Dialogue Manager & Intent Categorizer for Laya Browser Agent.

Categorizes user prompts in multi-turn interactive chat sessions to distinguish
between new goals, user feedback/corrections about prior steps, and status queries.
Triggers automated self-healing cache eviction when negative feedback is received
to prevent replaying flawed subtask macros or cached decisions.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .cache import extract_parent_scope, global_prompt_cache


CORRECTION_PATTERNS = [
    r"\binstead of\b",
    r"\bnot\s+(?:placed|working|done|created|updated|found|saved|added|showing|visible)\b",
    r"\bdidn'?t\b",
    r"\bwasn'?t\b",
    r"\bfailed\b",
    r"\bwrong\b",
    r"\berror\b",
    r"\bwhy\s+(?:did|were|is|was)\b",
    r"\bstill\s+showing\b",
    r"\bmissing\b",
    r"\bwhere\s+is\b",
    r"\bnot\s+all\b",
    r"\bunexpected\b",
    r"\bincorrect\b",
    r"\bdoesn'?t\s+work\b",
]

STATUS_PATTERNS = [
    r"(?:show|view|open|give|display|check|get)(?:\s+me)?(?:\s+the|\s+latest|\s+current)?\s+(?:report|status|progress|session|cache|summary)\b",
    r"^(?:show|view|open|check|what\s+is)?\s*(?:the\s+)?(?:report|status|session|progress|cache)$",
    r"what\s+did\s+you\s+do",
    r"what\s+happened",
    r"how\s+many\s+steps",
]

PLEASANTRY_PATTERNS = [
    r"^(?:thanks(?:\s+a\s+lot)?|thank\s+you(?:\s+so\s+much|\s+very\s+much|\s+a\s+lot)?|thx|ok|okay|cool|great|awesome|nice|sounds\s+good|looks\s+good|got\s+it|perfect|bye|goodbye|exit|quit|q)(?:[!\.\s]*)$",
    r"^(?:hello|hi|hey|good\s+(?:morning|afternoon|evening))(?:[!\.\s]*)$",
]


@dataclass
class DialogueTurn:
    """Classified conversational turn with remediation and cache invalidation metadata."""

    category: str  # "feedback_correction", "new_goal", "query_status", "conversational_basic"
    confidence: float
    reason: str
    remediation_needed: bool
    evicted_macros: int = 0
    evicted_prompts: int = 0


def classify_dialogue_turn(
    user_prompt: str,
    session_context: Optional[Dict[str, Any]] = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: Optional[str] = None,
) -> DialogueTurn:
    """Categorizes human prompt and triggers automated self-healing cache invalidation upon feedback."""
    clean = (user_prompt or "").strip().lower()
    last_goal = (session_context or {}).get("last_goal", "")
    target_url = (session_context or {}).get("target_url", "")

    # 1. Immediate Pleasantries / Basics
    if any(re.search(p, clean) for p in PLEASANTRY_PATTERNS):
        return DialogueTurn(
            category="conversational_basic",
            confidence=0.99,
            reason="User pleasantry or navigation command.",
            remediation_needed=False,
        )

    # 2. Immediate Status / Report Queries
    if any(re.search(p, clean) for p in STATUS_PATTERNS):
        return DialogueTurn(
            category="query_status",
            confidence=0.95,
            reason="User requesting status or execution report inspection.",
            remediation_needed=False,
        )

    # 3. Fast linguistic check for negative feedback / outcome discrepancy
    has_prior_goal = bool(last_goal)
    has_correction_pattern = any(re.search(p, clean) for p in CORRECTION_PATTERNS)

    if has_prior_goal and has_correction_pattern:
        # User is reporting an error in the previous run. Invalidate flawed cache entries!
        evicted_macros = 0
        evicted_prompts = 0
        if target_url:
            origin, route = extract_parent_scope(target_url)
            evicted_macros = global_prompt_cache.invalidate_route_macros(origin, route)
        evicted_prompts = global_prompt_cache.invalidate_recent_prompts(["task_planner", "screen_locator", "progressive_expansion"], limit=5)

        if session_context is not None:
            session_context["mode"] = "remediation"
            session_context["feedback_reason"] = user_prompt

        return DialogueTurn(
            category="feedback_correction",
            confidence=0.90,
            reason=f"Detected error report / outcome discrepancy regarding: '{last_goal}'",
            remediation_needed=True,
            evicted_macros=evicted_macros,
            evicted_prompts=evicted_prompts,
        )

    # 4. Default: Fresh autonomous goal
    if session_context is not None:
        session_context.pop("mode", None)
        session_context.pop("feedback_reason", None)

    return DialogueTurn(
        category="new_goal",
        confidence=0.90,
        reason="Fresh user automation instruction.",
        remediation_needed=False,
    )
