# Laya Browser Agent — Project Rules & Architectural Learnings

This repository houses **Laya Browser Agent**, a zero-cost, sub-second autonomous web agent running 100% locally on Apple Silicon Metal GPU via Laya MLX and WebMCP.

All agents, contributors, and subagents MUST strictly adhere to the following rules and architectural learnings.

---

## 1. Absolute Prohibition: Zero Hardcoded Domain Rules

### Rule:
**NEVER hardcode application-specific routes, entity names, action verbs, or domain assumptions in Python code or heuristic matchers.**

Examples of FORBIDDEN hardcoding:
- Hardcoding application URLs or paths (e.g. `/my-orders`, `/products`, `/admin`, `/dashboard`).
- Hardcoding specific entity names (e.g. `order`, `product`, `shoe`, `cart`, `inventory`).
- Hardcoding specific domain actions (e.g. `cancel_orders`, `approve_orders`, `buy_shoes`).
- Writing `if` statements matching application routes or specific mock entities.

### Correct Paradigm:
1. **Instruct, Don't Hardcode**: Delegate intent planning and semantic understanding to the **LLM Planner (`qwen2.5:3b`)** and **Local Decision Model (`laya-mlx`)** through prompt instructions and system recipes.
2. **Dynamic Affordance Discovery**: Discover links, buttons, inputs, and WebMCP tools dynamically from the live DOM and CDP affordance tree at runtime.
3. **Application-Agnostic Validation**: Write code that enforces structural boundaries (parent scope, live affordance checks, empty form guards) that work identically on **any web application** (GitHub, Amazon, Jira, Salesforce, PostQode).

---

## 2. Architectural Role Separation

The architecture is divided into three distinct tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. Intent & Planning Layer (Local LLM: qwen2.5:3b)                     │
│    • Decomposes requests into confident initial horizon steps          │
│    • Preserves exact user destination entities (Scope Anchoring)       │
│    • Normalizes data inspection requests into discrete audit steps     │
│    • Grounds deferred intent dynamically from live settled screen      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 2. Semantic Actor & Policy Layer (Decision Model: laya-mlx on Metal)   │
│    • Sub-15ms local decision loop on Apple Silicon Metal GPU           │
│    • Resolves natural language grammar variations (e.g. "signout" vs   │
│      "logout") directly in semantic vector space                       │
│    • Evaluates candidate route macros and selects screen affordances   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 3. Validation & Cache Infrastructure (Python Code & Browser Native)    │
│    • Parent Scope Isolation: (origin, route, canonical_tokens)         │
│    • Live Affordance Verification: validates element exists on page    │
│    • Progressive Discovery Expansion: grounds deferred intent on live  │
│      settled DOM; omits impossible pagination loops on single-page data│
│    • Guardrail Policy Interception: blocks empty forms & unsafe states │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Caching & Memory Rules (Claude / Gemini Style)

### A. Full-Prompt Cache (`prompt_cache`)
- Application-agnostic SHA-256 fingerprinting based on canonicalized payload (recursive key sorting, whitespace normalization, volatile runtime stripping).
- Backed by standard library SQLite WAL (`~/.laya/prompt_cache.db`). Zero external daemons (No Redis).

### B. Parent-Scoped Subtask Macro Cache (`subtask_cache`)
- **Parent Scope Mandatory**: All cached macros MUST be partitioned by `(origin, route, canonical_tokens)`.
  - `origin = scheme://netloc` (hard cryptographic boundary). Actions from `localhost:3000` must NEVER bleed into `github.com`.
  - `route = path` (e.g. `/login` vs `/checkout`).
- **Application-Agnostic Token Signature**: `canonicalize_task_intent` removes grammatical stop words (`a`, `the`, `my`, `all`, `to`, `go`) and sorts tokens. Never use keyword lookups for specific domain verbs.
- **Decision Model Semantic Gate**: When phrasing varies, the Decision Model (`laya-mlx`) evaluates available route macros against the subtask in `<15ms`.
- **Live Affordance Validation**: Before replaying any cached action, verify that the target element or tool exists on the current screen (`verify_macro_affordance`). If missing, immediately invalidate the entry.

### C. Self-Healing Cache Eviction on User Feedback & Outcome Discrepancies
- When a user conversational turn provides negative feedback or reports an outcome discrepancy (e.g. action failed, wrong result, item missing):
  1. The dialogue layer automatically categorizes the turn as `feedback_correction`.
  2. The cache layer MUST immediately evict all cached subtask macros for that `(origin, route)` (`invalidate_route_macros`) and purge recent prompt cache entries (`invalidate_recent_prompts`).
  3. Replaying cached actions that produced or permitted an error is strictly forbidden.
  4. The planner enters `remediation` mode: it conducts a live DOM/affordance audit to verify actual state before taking any corrective step, and forbids extracting parameter quantities from negative complaints.

---

## 4. Autonomous Data Inspection & Audit Rule

- When users request metric counts or data checks (e.g. *"check if there are any pending orders and include the count in the report"*):
  1. Planner forms a discrete subtask: `Check and count <target_entity>`.
  2. The agent executes WebMCP read queries (`get_*`, `list_*`) matching the target entity or falls back to live DOM table inspection (`table tr:has-text('<status>')`).
  3. Structured findings are captured in `agent.extracted_data` and surfaced in both the console receipt and the Markdown execution report under `### 📊 Extracted Findings & Data Audit`.
  4. Never skip or drop data inspection subtasks.

---

## 5. Lazy Senior Developer Engineering Principles

1. **YAGNI & Deletion over Addition**: The best code is the code never written. Before adding abstractions or dependencies, verify if standard library (`sqlite3`, `urllib.parse`, `re`) covers it.
2. **Root Cause over Symptom**: Fix shared logic at the source. If a model skips a step or misinterprets grammar, adjust the system recipe or affordance scoring once, rather than patching individual callers.
3. **One Runnable Check**: Every non-trivial change must be accompanied by an assert-based test in `tests/test_self_check.py`. All tests must stay green.
4. **Preserve Documentation**: Keep `doc/architecture.md`, `doc/product.md`, and `README.md` synchronized with actual implementation details.

---

## 6. Codebase Scalability, Cleanliness & Anti-Bloat Guardrails

### A. Strict Modular Size Limit (No Bloated Files)
- **Hard Limit**: Production source files MUST NOT exceed **500–600 lines** without modular separation. Monolithic files exceeding 1,000 lines are strictly forbidden.
- **Single Responsibility Principle (SRP)**:
  - **Core Orchestrator (`agent.py`)**: High-level step coordination, session lifecycle, and event dispatch.
  - **Reporting & Dashboards (`reporting.py`)**: Markdown reports and responsive HTML execution dashboards. Must not be embedded in the core agent class.
  - **Tool & Entity Resolution (`resolver.py`)**: WebMCP schema parameter binding, slot mapping, and prefix expansion algorithms.
  - **Screen Decision Engine (`laya_engine.py`)**: Metal GPU decision policy evaluation and candidate scoring.
  - **Affordance Perception & Search Matching (`affordance.py`)**: CDP element extraction, search field detection, and candidate labeling.
  - **Progressive Discovery & Remediation (`expansion.py`)**: Grounding deferred intent from live screens and screen failure recovery.
  - **Prompt & Macro Caches (`cache.py`)**: SQLite WAL persistence and cryptographic cache partitioning.

### B. Zero Duplicate & Zero Dead Code
1. **No Dead Code**: Never commit unused imports, uncalled private helpers, unreachable branches, obsolete regex patterns, commented-out code blocks, or zombie fallback matchers.
2. **No Duplicate Logic**: Shared helpers (URL normalization, token extraction, slot mapping, candidate normalization, status evaluation) must live in their designated module once. Never duplicate logic or copy-paste utility functions across files.
3. **Mandatory Pre-Commit Linter & Cleanliness Gate**:
   - Before completing any task or commit, the entire codebase MUST pass compilation (`python -m py_compile`) and strict linter audit (`flake8 --select=E9,F63,F7,F82,F821,F401,F841`) with **zero errors and zero warnings**.
   - Undefined variables (e.g. unimported `logger` or missing globals) are strictly prohibited.
   - Run automated dead code detection (checking unused imports via `F401` and unused assignments via `F841`) and proactively eliminate all dead references before staging.

---

## 7. Mandatory Documentation Synchronization on Meaningful Iterations

### Rule:
**Every meaningful code iteration, architectural refactoring, new capability, or bug resolution MUST be accompanied by an immediate update to the repository documentation.**

1. **Required Files**:
   - `doc/architecture.md`: Architecture specification, module boundaries, data flows, and failure modes.
   - `doc/product.md`: Product capabilities, metrics, and comparisons against alternatives.
   - `README.md`: Quick-start instructions, command options, and core capability highlights.
2. **Timing**: Update documentation in the **same turn and commit** as the code changes. Never leave documentation as a deferred follow-up.
3. **Accuracy Standard**: Documentation must describe working, tested implementations. Stale function names, obsolete parameters, or aspirational descriptions not backed by runnable code are strictly prohibited.

---

## 8. Universal Machine & Model Portability

### Rule:
**Laya Browser Agent MUST run seamlessly on any capable machine where local models can run (Apple Silicon Metal GPU, Linux x86_64/ARM64 with CUDA/ROCm/CPU, Windows WSL2/DirectML/CPU), without being coupled to a specific developer workstation or hardware architecture.**

1. **Graceful Acceleration Fallback**:
   - `laya_mlx` is an optimized accelerator on Apple Silicon Metal. On non-macOS platforms or environments where `mlx` is not installed, the engine MUST gracefully fallback to CPU or local model runners (Ollama / Llama.cpp / vLLM) without crashing on import or initialization.
   - Never use unconditional top-level imports of platform-specific libraries.
2. **Dynamic Local Model Discovery**:
   - Do not assume a hardcoded local model is always present. The agent queries `/api/tags` on the local model server (e.g. Ollama) and selects the best available local model if the requested model is not found, or respects the `OLLAMA_MODEL` environment variable.
3. **Cross-Platform OS & Environment Independence**:
   - File paths must use `os.path` / `pathlib.Path` standard library conventions (expanding `~` properly across POSIX and Windows).
   - Dynamic User-Agent and viewport strings must reflect generic desktop clients across macOS, Linux, and Windows (`platform.system()`) rather than hardcoded machine identifiers.
   - Zero workstation-specific paths, environment variables, or hardcoded ports.


