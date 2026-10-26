"""Task Specification Loader for Laya Benchmark.

Parses YAML task definitions from bench/tasks/ matching the schema in doc/plan.md §4.3.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

TASKS_DIR = Path(__file__).parent / "tasks"


@dataclass
class BenchmarkTask:
    """Represents a benchmark test task."""

    id: str
    tier: str  # T1, T2, T3, T4, T5
    site: str  # e.g. fixture/shop, fixture/admin, nexus, https://...
    goal: str
    oracle: Dict[str, Any]
    max_steps: int = 25
    tags: List[str] = field(default_factory=list)
    split: str = "heldout"  # train | heldout
    file_path: Optional[str] = None


def load_benchmark_tasks(
    tier: Optional[str] = None,
    split: Optional[str] = None,
    tasks_dir: Optional[Path] = None,
) -> List[BenchmarkTask]:
    """Loads all matching tasks from the tasks directory."""
    dir_path = tasks_dir or TASKS_DIR
    if not dir_path.exists():
        return []

    loaded: List[BenchmarkTask] = []
    for f in sorted(dir_path.glob("*.yaml")):
        try:
            with open(f, "r", encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
                if not isinstance(data, dict):
                    continue

                t_tier = str(data.get("tier", "T2")).upper()
                t_split = str(data.get("split", "heldout")).lower()

                if tier and t_tier != tier.upper():
                    continue
                if split and split.lower() != "all" and t_split != split.lower():
                    continue

                task = BenchmarkTask(
                    id=str(data.get("id", f.stem)),
                    tier=t_tier,
                    site=str(data.get("site", "fixture/shop")),
                    goal=str(data.get("goal", "")),
                    oracle=dict(data.get("oracle", {})),
                    max_steps=int(data.get("max_steps", 25)),
                    tags=list(data.get("tags", [])),
                    split=t_split,
                    file_path=str(f),
                )
                loaded.append(task)
        except Exception as e:
            raise ValueError(f"Failed to load benchmark task {f}: {e}") from e

    return loaded
