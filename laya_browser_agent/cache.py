"""Local Prompt & Context Cache Layer (Inspired by Claude / Gemini Prompt Caching).

Provides general-purpose, application-agnostic caching for local LLM prompts, plans,
and vision-routing evaluations. Canonicalizes inputs so identical or semantically
equivalent prompt payloads hit the cache in <0.2ms, avoiding redundant model re-runs.

Backed by local SQLite (Python standard library) with zero external service dependencies.
"""

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple


CACHE_DIR = os.environ.get("LAYA_HOME") or os.path.expanduser("~/.laya")
DB_FILE = os.path.join(CACHE_DIR, "prompt_cache.db")


def extract_parent_scope(url: str) -> Tuple[str, str]:
    """Extracts (origin, route_pattern) from URL to isolate subtasks by application and page.

    Example:
    'http://localhost:3000/my-orders?tab=active#details' -> ('http://localhost:3000', '/my-orders')
    """
    if not url:
        return "default", "/"
    try:
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "default"
        route = parsed.path.rstrip("/") or "/"
        return origin, route
    except Exception:
        return "default", "/"


STOP_WORDS = {
    "a", "an", "the", "to", "for", "in", "on", "at", "by", "from",
    "my", "our", "your", "their", "all", "any", "some", "this", "that",
    "please", "then", "and", "also", "after", "go", "open", "visit", "with",
}


def canonicalize_task_intent(subtask: str) -> str:
    """Application-agnostic token normalization for subtask text.

    Removes punctuation and generic functional stop words, producing an order-invariant token signature
    without hardcoding any domain routes, entities, or application-specific nouns.
    Semantic variation and grammar mapping are delegated to the Decision Model and LLM.
    """
    if not subtask or not isinstance(subtask, str):
        return "root"
    cleaned = re.sub(r"[^\w\s]", " ", subtask.lower())
    tokens = [w for w in cleaned.split() if w and w not in STOP_WORDS]
    if not tokens:
        tokens = cleaned.split() or ["root"]
    return "_".join(sorted(set(tokens)))


def normalize_canonical_intent(subtask: str) -> str:
    """Domain-agnostic intent normalizer (delegates to canonicalize_task_intent)."""
    return canonicalize_task_intent(subtask)


def canonicalize_text(text: str) -> str:
    """Normalizes text by trimming whitespace, normalizing quotes, and collapsing spaces."""
    if not isinstance(text, str):
        return str(text)
    # Normalize unicode curly quotes
    t = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    # Collapse multiple whitespace/newlines
    t = re.sub(r"\s+", " ", t).strip()
    return t


def canonicalize_payload(payload: Any) -> Any:
    """Recursively canonicalizes arbitrary prompt payloads (dicts, lists, primitives)

    ensuring deterministic JSON serialization regardless of dictionary insertion order.
    """
    if isinstance(payload, dict):
        # Sort keys and strip volatile dynamic values (e.g. timestamps)
        clean = {}
        for k in sorted(payload.keys()):
            if k in ("timestamp", "created_at", "latency_ms", "duration"):
                continue
            clean[str(k)] = canonicalize_payload(payload[k])
        return clean
    elif isinstance(payload, (list, tuple)):
        return [canonicalize_payload(item) for item in payload]
    elif isinstance(payload, str):
        return canonicalize_text(payload)
    else:
        return payload


def compute_prompt_hash(namespace: str, model: str, payload: Any) -> Tuple[str, str]:
    """Generates a deterministic SHA-256 fingerprint for canonicalized prompt input.

    Returns: (cache_key, canonical_json_string)
    """
    canonical = canonicalize_payload(payload)
    canonical_str = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    hasher = hashlib.sha256()
    hasher.update(f"{namespace}:{model}:{canonical_str}".encode("utf-8"))
    cache_key = hasher.hexdigest()
    return cache_key, canonical_str


class LocalPromptCache:
    """High-speed persistent prompt & context cache for local model inference.
    
    Acts like Claude / Gemini prompt caching for local agents: ensures identical
    or normalized prompt structures are never re-evaluated from scratch.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_FILE
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            return conn
        except Exception:
            # Fallback to current directory if user home is not writable
            fallback_path = os.path.join(os.getcwd(), ".prompt_cache.db")
            conn = sqlite3.connect(fallback_path, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            return conn

    def _init_db(self):
        """Initializes cache tables if they do not exist."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prompt_cache (
                        cache_key TEXT PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        model TEXT NOT NULL,
                        canonical_input TEXT NOT NULL,
                        response_json TEXT NOT NULL,
                        hit_count INTEGER DEFAULT 0,
                        avg_latency_saved_ms REAL DEFAULT 1500.0,
                        created_at REAL NOT NULL,
                        last_accessed REAL NOT NULL
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_namespace ON prompt_cache(namespace);")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subtask_cache (
                        origin TEXT NOT NULL,
                        route TEXT NOT NULL,
                        canonical_intent TEXT NOT NULL,
                        macro_json TEXT NOT NULL,
                        hit_count INTEGER DEFAULT 0,
                        created_at REAL NOT NULL,
                        last_verified REAL NOT NULL,
                        PRIMARY KEY (origin, route, canonical_intent)
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_subtask_scope ON subtask_cache(origin, route);")
                conn.commit()
        except Exception:
            pass

    def get(self, namespace: str, model: str, payload: Any) -> Optional[Any]:
        """Retrieves cached response for canonical prompt input. Returns None on miss."""
        try:
            cache_key, _ = compute_prompt_hash(namespace, model, payload)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT response_json, hit_count FROM prompt_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                )
                row = cursor.fetchone()
                if row:
                    resp_json, hit_count = row
                    # Update hit count and access time
                    cursor.execute(
                        """
                        UPDATE prompt_cache
                        SET hit_count = hit_count + 1, last_accessed = ?
                        WHERE cache_key = ?
                        """,
                        (time.time(), cache_key),
                    )
                    conn.commit()
                    return json.loads(resp_json)
        except Exception:
            pass
        return None

    def set(
        self,
        namespace: str,
        model: str,
        payload: Any,
        response: Any,
        latency_saved_ms: float = 1500.0,
    ):
        """Stores model response indexed by deterministic canonical prompt hash."""
        try:
            cache_key, canonical_str = compute_prompt_hash(namespace, model, payload)
            resp_json = json.dumps(response, ensure_ascii=False)
            now = time.time()
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO prompt_cache
                        (cache_key, namespace, model, canonical_input, response_json, hit_count, avg_latency_saved_ms, created_at, last_accessed)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        response_json = excluded.response_json,
                        last_accessed = excluded.last_accessed;
                    """,
                    (cache_key, namespace, model, canonical_str, resp_json, float(latency_saved_ms), now, now),
                )
                conn.commit()
        except Exception:
            pass

    def get_subtask_macro(self, origin: str, route: str, subtask: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached subtask macro scoped to (origin, route, canonical_intent)."""
        try:
            canonical_intent = normalize_canonical_intent(subtask)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT macro_json, hit_count FROM subtask_cache
                    WHERE origin = ? AND route = ? AND canonical_intent = ?
                    """,
                    (origin, route, canonical_intent),
                )
                row = cursor.fetchone()
                if row:
                    macro_json, hit_count = row
                    cursor.execute(
                        """
                        UPDATE subtask_cache
                        SET hit_count = hit_count + 1, last_verified = ?
                        WHERE origin = ? AND route = ? AND canonical_intent = ?
                        """,
                        (time.time(), origin, route, canonical_intent),
                    )
                    conn.commit()
                    return json.loads(macro_json)
        except Exception:
            pass
        return None

    def set_subtask_macro(self, origin: str, route: str, subtask: str, macro: Dict[str, Any]):
        """Caches action macro scoped to (origin, route, canonical_intent)."""
        try:
            canonical_intent = normalize_canonical_intent(subtask)
            macro_json = json.dumps(macro, ensure_ascii=False)
            now = time.time()
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO subtask_cache
                        (origin, route, canonical_intent, macro_json, hit_count, created_at, last_verified)
                    VALUES (?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(origin, route, canonical_intent) DO UPDATE SET
                        macro_json = excluded.macro_json,
                        last_verified = excluded.last_verified;
                    """,
                    (origin, route, canonical_intent, macro_json, now, now),
                )
                conn.commit()
        except Exception:
            pass

    def invalidate_subtask_macro(self, origin: str, route: str, subtask: str):
        """Invalidates a subtask macro when DOM affordance validation fails."""
        try:
            canonical_intent = normalize_canonical_intent(subtask)
            with self._get_connection() as conn:
                conn.execute(
                    """
                    DELETE FROM subtask_cache
                    WHERE origin = ? AND route = ? AND canonical_intent = ?
                    """,
                    (origin, route, canonical_intent),
                )
                conn.commit()
        except Exception:
            pass

    def invalidate_route_macros(self, origin: str, route: str) -> int:
        """Evicts all cached subtask macros for a specific (origin, route) parent scope upon failure/correction."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM subtask_cache WHERE origin = ? AND route = ?",
                    (origin, route),
                )
                evicted = cursor.rowcount
                conn.commit()
                return evicted
        except Exception:
            return 0

    def invalidate_recent_prompts(self, namespaces: Optional[List[str]] = None, limit: int = 5) -> int:
        """Evicts most recent prompt cache entries so failed plans or routing choices are not replayed."""
        try:
            ns_list = namespaces or ["task_planner", "screen_locator"]
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in ns_list)
                cursor.execute(
                    f"""
                    DELETE FROM prompt_cache
                    WHERE cache_key IN (
                        SELECT cache_key FROM prompt_cache
                        WHERE namespace IN ({placeholders})
                        ORDER BY created_at DESC LIMIT ?
                    )
                    """,
                    (*ns_list, limit),
                )
                evicted = cursor.rowcount
                conn.commit()
                return evicted
        except Exception:
            return 0

    def get_subtask_macros_for_route(self, origin: str, route: str) -> List[Dict[str, Any]]:
        """Retrieves all cached macros recorded for a specific (origin, route) parent scope."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT canonical_intent, macro_json FROM subtask_cache
                    WHERE origin = ? AND route = ?
                    ORDER BY hit_count DESC LIMIT 5
                    """,
                    (origin, route),
                )
                rows = cursor.fetchall()
                results = []
                for intent, m_json in rows:
                    try:
                        m_data = json.loads(m_json)
                        m_data["_intent"] = intent
                        results.append(m_data)
                    except Exception:
                        pass
                return results
        except Exception:
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Returns cache utilization statistics."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), SUM(hit_count), SUM(hit_count * avg_latency_saved_ms) FROM prompt_cache;")
                total_entries, total_hits, total_latency_saved = cursor.fetchone()
                total_entries = total_entries or 0
                total_hits = total_hits or 0
                total_latency_saved = (total_latency_saved or 0.0) / 1000.0

                cursor.execute("SELECT COUNT(*), SUM(hit_count) FROM subtask_cache;")
                st_row = cursor.fetchone()
                st_entries = st_row[0] if st_row else 0
                st_hits = st_row[1] if st_row else 0
                st_entries = st_entries or 0
                st_hits = st_hits or 0

                return {
                    "total_cached_prompts": total_entries,
                    "total_cache_hits": total_hits,
                    "total_cached_subtasks": st_entries,
                    "total_subtask_hits": st_hits,
                    "estimated_time_saved_seconds": round(total_latency_saved + (st_hits * 1.5), 2),
                    "storage_path": self.db_path,
                }
        except Exception:
            return {
                "total_cached_prompts": 0,
                "total_cache_hits": 0,
                "total_cached_subtasks": 0,
                "total_subtask_hits": 0,
                "estimated_time_saved_seconds": 0.0,
                "storage_path": self.db_path,
            }

    def clear(self):
        """Clears all cached prompt and subtask entries."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM prompt_cache;")
                conn.execute("DELETE FROM subtask_cache;")
                conn.commit()
        except Exception:
            pass


# Global singleton instance for application-wide prompt caching
global_prompt_cache = LocalPromptCache()
