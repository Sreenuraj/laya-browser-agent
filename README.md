# Laya Browser Agent

> Zero-cost, sub-second autonomous web agent running 100% locally on Apple Silicon Metal GPU (or Linux / Windows CPU / CUDA) via Laya MLX and WebMCP.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Cross-Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-blue.svg)](#)
[![Cloud Cost: $0.00](https://img.shields.io/badge/Cloud%20Cost-%240.00-brightgreen.svg)](#)
[![Decision Latency: <15ms](https://img.shields.io/badge/Decision%20Latency-%3C15ms-blue.svg)](#)

---

## Overview

Laya Browser Agent is an autonomous web agent that executes tasks directly in the browser with zero cloud API dependencies. It combines a local intent planner (Ollama / local runners), a sub-15ms decision model (Laya MLX on Apple Silicon Metal GPU with graceful CPU/Ollama fallback), and Playwright for WebMCP and DOM actuation.

### Key Capabilities

- **Zero Cloud API Spend**: All intent planning, route selection, and element interaction execute locally on device.
- **Sub-15ms Decision Loop**: Powered by Laya MLX on Metal GPU in a single forward pass without autoregressive token generation.
- **Universal Machine & Model Portability**: Runs seamlessly across Apple Silicon (Metal GPU), Linux x86_64/ARM64 (CUDA/CPU), and Windows WSL2. Automatically discovers local models via `/api/tags` with graceful CPU fallback.
- **Machine-Actionable Sequential Planning**: Converts human requests into concrete atomic browser actions (`Enter '<query>' into search input box and submit`, `Inspect search results and note the price of '<item>'`) without conversational ambiguity.
- **Truthful Status Reporting**: Deterministic status evaluation eliminates false-positive success reports; subtasks truthfully display `[✓]` (done), `[✗]` (unfulfillable / failed with reason), and `[○]` (pending).
- **Progressive Discovery Horizon Planning**: Partitions complex tasks into a ~100% confident initial horizon (navigation, credentials, search) and deferred intent, grounding downstream actions strictly from live settled screen affordances to eliminate speculative UI hallucinations.
- **Dynamic Intent & Affordance Discovery**: Discovers WebMCP tools (`window.navigator.modelContext`, `<form toolname="...">`) and live DOM interactive elements at runtime without hardcoded application routes or domain rules.
- **Two-Tier Local Cache (SQLite WAL)**: High-speed caching for LLM prompt plans and parent-scoped subtask action macros (`~/.laya/prompt_cache.db`), enabling sub-millisecond replay with live affordance validation and self-healing eviction.
- **Universal Multi-Field Form Entry**: Seamlessly handles contact forms, registrations, settings, and feedback inputs (`name`, `email`, `phone`, `address`, `message`, `subject`) with automated hyphen/underscore normalization and label matching.
- **Loop Engineering Fault Isolation & Zero-Crash Execution**: Step-level exception isolation ensures unhandled DOM errors or runtime exceptions never crash the session; the agent invokes local models (`qwen2.5` / MLX) to remediate plans, adapt subtasks, and advance through multi-site goals.
- **Proactive & Reactive Overlay Dismissal**: Automatically dismisses blocking cookie banners, newsletter prompts, and login walls (e.g. Flipkart's `✕` modal) during settling, with native `Escape` key clearance and automatic retry upon click interception.
- **Autonomous Data Auditing & Information Synthesis**: Audits and extracts quantitative metrics, tables, card lists, or structured entity data across single-page and multi-site workflows with container-based content extraction.
- **Minimalist Session Telemetry**: Tracks token usage (input/output), prompt and macro cache hits/writes, saved latencies, and step latencies in a minimalist monospace table without visual clutter.
- **Universal Variant Perception & Cross-Site Consistency Anchoring**: Detects specifications (storage, shoe/apparel sizes, colors, travel class), filters out accessories and sponsored ad outliers, and anchors selected variants across multi-site tasks (`selected_variant`) for exact apples-to-apples repeatability.
- **Interactive Disambiguation**: Prompts users in CLI mode when multiple variants exist on screen or selects unselected variant swatches before clicking "Add to Cart".
- **Frame Deduplication**: MD5 content-hash deduplication across raw browser frames suppresses duplicate PNG screenshot files on disk and eliminates redundant visual verifications.
- **Multi-Turn Conversational Memory**: Preserves credentials, routes, and slots across turns with an $O(1)$ token overhead (~40 tokens) while isolating ephemeral DOM and browser traces.
- **Multi-Turn Outcome Grounding & Symmetric Tab Planning**: Automatically bases subsequent turn tasks directly on the outcome of previous turns (status, findings, established slots, and open tabs) with universal anaphoric pronoun resolution ("it", "them", "that") and symmetric tab execution, while pruning conversational tab retention constraints.
- **Local Screen Arbitration & Unavailable Action Guard (`action_blocked`)**: Evaluates on-screen affordances using a local screen arbitrator. When physical screen states indicate an item or action cannot be completed (e.g. "Notify Me", out of stock, coming soon), the agent avoids hallucinated fallback clicks (such as clicking navbar icons or compare checkboxes), flags the subtask as `unfulfillable` in the execution report for user action, and advances cleanly to remaining subtasks.
- **Interactive Clarification**: Prompts for missing credentials or parameters upfront, cleanly binding answers to the required fields.
- **True Multi-Tab Browser Orchestration & Multi-Turn Tab Continuity**: Supports native parallel browser tabs (`new_tab`, `switch_tab`, `get_tabs`, `close_tab`), allowing agents to operate across multiple sites concurrently. In multi-turn workflows, the agent passes open tab context across prompts and switches tabs dynamically without re-navigating from scratch or closing the browser.
- **Closed-Loop Subtask Verification & Anti-Stagnation**: Accurately distinguishes text input demands from operational actions (e.g. `add_to_cart`, `buy`, `order`, `cancel`, `approve`, `switch_tab`). When an operational action completes successfully, the subtask advances immediately even when target entities are referenced. Anti-stagnation budgets and transient cache eviction prevent cyclic doom loops.
- **Guarded Subtask Macro Caching**: Enforces a strict advancement gate: macros are cached *only* after a subtask is verified to advance. Actions that fail or stagnate are isolated in `failed_actions` and existing macros are evicted to prevent repetitive action loops.
- **Non-Existent Product & Accessory Trap Defense**: Distinguishes genuine product listings from accessories (cases, covers, screen guards). If an item is unreleased or not listed on a site, the agent prompts for clarification or logs an audit finding rather than clicking compare checkboxes or buying phone covers.
- **Clean Modular Scalability**: Enforces a strict 500–600 line modularity ceiling with decoupled components (`affordance.py`, `expansion.py`, `reporting.py`, `resolver.py`, `laya_engine.py`, `cache.py`, `variants.py`).

---

## System Requirements & Prerequisites

| Component | Minimum Specification | Recommended Specification |
| :--- | :--- | :--- |
| **Operating System** | macOS 13+ (Ventura), Linux (Ubuntu 20.04+, Debian 11+), Windows 10/11 (WSL2) | macOS 14+ (Sonoma / Sequoia) or Linux Ubuntu 24.04 LTS |
| **Processor & GPU** | 4-Core x86_64 / ARM64 CPU | Apple Silicon (M1/M2/M3/M4) for native Metal GPU acceleration (<15ms per step) or NVIDIA CUDA GPU |
| **System Memory (RAM)**| 8 GB Unified Memory / RAM | 16 GB+ Unified Memory / RAM (for concurrent local LLM execution) |
| **Storage** | 2 GB free disk space | 5 GB free disk space (for Chromium binaries and local model weights) |
| **Python** | Python 3.11 or 3.12 (64-bit) | Python 3.12 |
| **Browser Runtime** | Playwright Chromium (auto-provisioned by `./run.sh`) | Playwright Chromium (auto-provisioned by `./run.sh`) |
| **Local Model Runner** | Deterministic Heuristic Regex Mode (Zero Daemons) | [Ollama](https://ollama.ai) (`http://localhost:11434`) with `qwen2.5:3b` or `qwen2.5:7b` |

---

## Setup & Installation

### Option A: Zero-Touch Auto-Provisioning (Recommended)
You do not need to manually configure virtual environments or install browser binaries. Running `./run.sh` automatically performs pre-flight checks, creates `.venv`, installs OS-appropriate dependencies from `requirements.txt`, provisions the Chromium browser binary, pre-caches the local model if on Apple Silicon, and launches the agent:

```bash
./run.sh
```

### Option B: One-Step Setup Script
Run the automated environment setup script:
```bash
./setup.sh
```

### Option C: Manual Step-by-Step Installation
For custom virtual environments or CI/CD pipelines:

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Sreenuraj/laya-browser-agent.git
   cd laya-browser-agent
   ```

2. **Create and activate Python virtual environment**:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Python dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
   *(Note: `requirements.txt` includes PEP 508 environment markers. On Apple Silicon macOS, `mlx` and `laya-mlx` are installed for native Metal GPU acceleration; on Linux and Windows WSL2, the agent installs cross-platform libraries and falls back gracefully to CPU / Ollama).*

4. **Install Playwright Chromium browser binary**:
   ```bash
   playwright install chromium
   ```
   *Linux Note: If running on a headless Linux server or container, install required system shared libraries:*
   ```bash
   playwright install --with-deps chromium
   ```

5. **Pull Local Planner Model (Ollama)**:
   Ensure [Ollama](https://ollama.ai) is installed and running, then pull your preferred model:
   ```bash
   ollama pull qwen2.5:3b
   # or for higher precision planning:
   ollama pull qwen2.5:7b
   ```

---

## Currently Tested Against

Laya Browser Agent is continuously verified against real-world applications, diverse hardware platforms, and local model backends:

### 1. Operating Systems & Hardware Platforms
- **macOS Sequoia 15.x / Sonoma 14.x**: Tested on Apple Silicon (M1 Max, M2 Pro, M3 Max) with Apple Metal GPU acceleration (<15ms decision latency).
- **Linux (Ubuntu 22.04 LTS / 24.04 LTS)**: Tested on x86_64 and ARM64 with CPU and Ollama runner fallback.
- **Windows 11 (WSL2 - Ubuntu 22.04)**: Tested in WSL2 virtualized environment with Playwright headed and headless execution.

### 2. Local Models & Decision Engines
- **Decision Engine (System 1)**: `aac6fef/laya-mlx` (Metal GPU, single-forward-pass bidirectional encoder, ~322M parameters) with graceful fallback to local LLM choice.
- **Local Intent Planners (System 2)**:
  - `qwen2.5:3b` (Default fast local planner, ~300ms decomposition)
  - `qwen2.5:7b` (High-precision enterprise parameter extraction)
  - `llama3.2:3b` (Alternative lightweight local model)
- **Zero-Daemon Fallback**: Deterministic heuristic regex extraction & rule-based decision loop (functions 100% offline with zero external services running).

### 3. Tested Target Applications & Live Environments
- **Enterprise E-Commerce**: **[PostQode Nexus](https://github.com/Sreenuraj/postqode-nexus)** (`http://localhost:3000`)
  - User authentication and role switching (user vs admin)
  - Dynamic product catalog search, pagination, and multi-item checkout
  - Admin command center: bulk order approvals and status inspections
  - Self-healing recovery from user corrections and state discrepancies
- **Multi-Site Commercial E-Commerce**:
  - **Amazon.in** (`https://amazon.in`) & **Flipkart.com** (`https://flipkart.com`): Multi-site concurrent search, universal currency extraction (`₹`), and cross-host price comparison determination.
- **Content & Technical Portals**:
  - **GitHub** (`https://github.com`): Repository search, issues and active pull request navigation.
  - **Hacker News** (`https://news.ycombinator.com`): Story inspection and headline auditing.
  - **Google** (`https://google.com`): Query submission and web search affordance resolution.

---

## Quick Start Execution

**Conversational Chat Mode (Default):**
```bash
./run.sh
```

**Direct Command Execution (Headed):**
```bash
./run.sh --task "Login as user (user/User@123), go to my-orders, cancel all open orders and logout" --headed
```

**Headless Execution (CI / Automated Testing):**
```bash
./run.sh --task "Login as admin (admin/Admin@123), go to /orders and approve all pending orders" --headless
```

---

## Interactive Chat Commands

When running `./run.sh`, you can enter automation tasks directly at `laya > ` or use these built-in commands:

| Command | Alias | Description |
| :--- | :--- | :--- |
| `help` | `?` | Show available commands and usage guide |
| `status` | `session` | View authenticated user, current route, and active session slots |
| `models` | `list models` | List installed Ollama models and currently active selection |
| `model <name>` | `use <name>` | Switch active local LLM model on the fly (e.g., `model qwen2.5:7b`) |
| `cache` | `cache stats` | View prompt and subtask macro cache hit counts and saved latency |
| `clear cache` | `flush cache` | Safely clear and truncate the SQLite cache database |
| `show report` | `report` | View summary and file paths of the latest execution report |
| `retry` | `rerun` | Re-run the previous automation goal |
| `reset` | `clear` | Reset conversational memory, credentials, and slots |
| `exit` | `quit`, `q` | Exit the CLI session |

---

## Local Cache Management

Laya Browser Agent uses SQLite with Write-Ahead Logging (WAL) for microsecond cache access with zero external services:

- **Database File**: `~/.laya/prompt_cache.db`
- **Full-Prompt Cache (`prompt_cache`)**: SHA-256 fingerprinting on canonicalized JSON payloads for local LLM planning (`<0.2ms` hit latency).
- **Subtask Macro Cache (`subtask_cache`)**: Caches action macros partitioned by `(origin, route, canonical_intent)` with live screen affordance validation before replay.
- **Self-Healing Eviction**: Purges route macros and recent prompts when the user reports an outcome discrepancy or negative feedback.

### Inspecting Cache
```bash
# Via interactive chat:
laya > cache

# Via SQLite CLI:
sqlite3 ~/.laya/prompt_cache.db "SELECT origin, route, canonical_intent, hit_count FROM subtask_cache;"
```

### Clearing Cache
```bash
# Via interactive chat:
laya > clear cache

# Via shell:
rm -f ~/.laya/prompt_cache.db*
```

---

## Audit Reports

Every execution session automatically writes verification reports to the `reports/` directory:
- **Interactive HTML Dashboard** (`reports/execution_report.html`): Responsive dark dashboard with executive status badges, session telemetry summary (tokens, cache reads/writes, latency savings), data audit tables, step latency telemetry, and deduplicated screenshots.
- **Structured Markdown Report** (`reports/execution_report.md`): Markdown documentation with masked credentials, minimalist monospace session telemetry, action audit trails, and execution summaries.

---

## Command-Line Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--url <URL>` | Inferred / `http://localhost:3000` | Target web application URL |
| `--task <GOAL>` | Chat Mode | Natural language goal to execute |
| `--headed` | Enabled | Launch visible Chromium browser window |
| `--headless` | Disabled | Run browser in background |
| `--model <ID>` | `aac6fef/laya-mlx` | MLX model identifier |
| `--ollama-url <URL>` | `http://localhost:11434` | Ollama service endpoint |
| `--ollama-model <NAME>` | Auto-resolved | Local LLM for extraction (prefers `qwen2.5:7b`, falls back to `3b`) |
| `--report` / `--no-report` | Enabled (`True`) | Generate HTML and Markdown execution reports |
| `--screenshot-mode` | `major_steps` | Screenshot frequency: `major_steps`, `every_step`, or `none` |
| `--report-dir <DIR>` | `reports` | Output directory for reports and screenshots |
| `--report-file <PATH>` | `reports/execution_report.md` | Path for Markdown execution report |

---

## Testing & Quality Gates

Run the automated quality gate (Python syntax compilation, zero-warning Flake8 lint gate, and deterministic self-check suite):
```bash
make check
```

Run the deterministic T1 offline test suite:
```bash
pytest tests/
```

### Practice Sites & Optional Task Runner (`bench/`)

Three small local web apps (a shop with WebMCP tools, an admin table, and a multi-step form with an overlay) for trying the agent by hand:

```bash
# Serve the practice sites on http://127.0.0.1:8765 (shop/, admin/, forms/)
python -m bench serve

# In another terminal, point the agent at one of them
./run.sh --url http://127.0.0.1:8765/shop/ --task "Log in as user (user/User@123) and order 2 Wireless Ergonomic Mouse" --headed
```

Each site exposes `/<site>/__state` (current app state as JSON) and `/<site>/__reset`. Set `LAYA_BENCH_PORT` to change the port.

The optional runner executes the ready-made tasks in `bench/tasks/` and checks the final app state for you:

```bash
python -m bench list --tier T2
python -m bench run --tier T2 --ids t2-forms-006,t2-shop-001 --task-timeout 120
python -m bench run --tier T3 --max-tasks 5   # needs PostQode Nexus on localhost:3000
```

Results go to `bench/results/` (git-ignored). Step logs are written to `$LAYA_HOME/trajectories/<run_id>.jsonl` (default `~/.laya`), with credentials masked. The test suite points `LAYA_HOME` at a temp directory, so tests never touch your real cache or logs.

Run the end-to-end multi-scenario suite against live PostQode Nexus:
```bash
./.venv/bin/python tests/test_scenarios_suite.py
```

---

## Open Source Libraries & Acknowledgements

Laya Browser Agent is built upon and inspired by exceptional open-source software. We gratefully acknowledge the following projects and their authors:

| Library / Project | Role & Purpose | License | Source / Website |
| :--- | :--- | :--- | :--- |
| **[Playwright](https://playwright.dev/)** | High-performance browser automation, CDP protocol integration, and Chromium lifecycle | Apache-2.0 | [microsoft/playwright-python](https://github.com/microsoft/playwright-python) |
| **[Laya MLX](https://github.com/aac6fef/laya-mlx)** | Sub-15ms local decision model execution on Apple Silicon Metal GPU | MIT / Apache-2.0 | [aac6fef/laya-mlx](https://github.com/aac6fef/laya-mlx) |
| **[Laya Browser Model](https://huggingface.co/convaiinnovations/laya)** | Foundational bidirectional encoder decision model for web affordances | Apache-2.0 | [convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya) / [cklxx/laya-browser](https://huggingface.co/cklxx/laya-browser) |
| **[Apple MLX](https://github.com/ml-explore/mlx)** | Native machine learning framework engineered for Apple Silicon unified memory | MIT | [ml-explore/mlx](https://github.com/ml-explore/mlx) |
| **[Ollama](https://ollama.ai/)** | Local model serving runtime for open-source instruction models | MIT | [ollama/ollama](https://github.com/ollama/ollama) |
| **[Qwen 2.5](https://github.com/QwenLM/Qwen2.5)** | Fast, highly-capable local language model for task planning and slot extraction | Apache-2.0 | [QwenLM/Qwen2.5](https://github.com/QwenLM/Qwen2.5) |
| **[Rich](https://github.com/Textualize/rich)** | Terminal formatting, interactive styling, receipt tables, and progress rendering | MIT | [Textualize/rich](https://github.com/Textualize/rich) |
| **[HTTPX](https://www.encode.io/httpx/)** | Asynchronous and synchronous HTTP client for communicating with local model servers | BSD-3-Clause | [encode/httpx](https://github.com/encode/httpx) |
| **[Pydantic](https://docs.pydantic.dev/)** | Schema definition, runtime validation, and structured plan serialization | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| **[SQLite](https://www.sqlite.org/)** | Embedded, serverless WAL-mode database for microsecond prompt and subtask macro caching | Public Domain | [sqlite.org](https://www.sqlite.org/) |

---

## License

This project is licensed under the [MIT License](LICENSE).

