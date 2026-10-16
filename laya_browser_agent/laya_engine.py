"""Laya MLX Decision Engine for WebMCP action routing."""

from typing import Any, Dict, List, Tuple
from laya_mlx import Agent


class LayaEngine:
    """Wraps Laya MLX decision agent to evaluate state and choose next WebMCP action."""

    def __init__(self, model_id: str = "aac6fef/laya-mlx", device: str = "gpu"):
        self.model_id = model_id
        self.agent = Agent(model_id, device=device)

    def decide_next_action(
        self,
        goal: str,
        current_url: str,
        available_tools: List[Dict[str, Any]],
        recent_history: List[str],
    ) -> Tuple[str, float, Dict[str, float]]:
        """
        Formulates a concise state (< 100 tokens) and choice criteria question,
        then queries Laya MLX on Metal GPU in sub-15ms.
        """
        if not available_tools:
            return "finish", 1.0, {"finish": 1.0}

        # Keep state representation ultra-compact to fit well inside 512-token limit
        history_summary = " -> ".join(recent_history[-3:]) if recent_history else "start"
        state = (
            f"Goal: {goal}\n"
            f"Current URL: {current_url}\n"
            f"Recent Actions: {history_summary}"
        )

        criteria = {}
        for tool in available_tools:
            name = tool.get("name")
            desc = tool.get("description", f"Call {name}")
            # Truncate long descriptions to stay concise
            if len(desc) > 80:
                desc = desc[:77] + "..."
            criteria[name] = desc

        criteria["finish"] = "All requirements of the goal have been satisfied"

        questions = {
            "action": {
                "type": "choice",
                "instructions": "Which action should be executed next to advance the goal?",
                "criteria": criteria,
            }
        }

        res = self.agent.predict(state, questions)
        answer = res.get("answers", {}).get("action", {})
        chosen = answer.get("choice", "finish")
        confidence = float(answer.get("confidence", 0.0))
        probabilities = answer.get("probabilities", {})

        return chosen, confidence, probabilities
