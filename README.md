# ⚡️ Laya Browser Agent

> **Zero-cost, sub-second autonomous web agent running 100% locally on Apple Silicon Metal GPU via Laya MLX and WebMCP.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Apple Silicon](https://img.shields.io/badge/Hardware-Apple%20Silicon%20(Metal%20GPU)-success.svg)](#)
[![Cloud Cost: $0.00](https://img.shields.io/badge/Cloud%20Cost-%240.00-brightgreen.svg)](#)
[![Decision Latency: <15ms](https://img.shields.io/badge/Decision%20Latency-%3C15ms-blue.svg)](#)

---

## 🌟 Highlights

- **$0.00 Cloud Spend**: Zero tokens sent to OpenAI or Anthropic. All intent planning, route selection, and actuation run locally on your machine.
- **Sub-15ms Decision Loop**: Powered by **Laya MLX** (`aac6fef/laya-mlx`) running directly on Apple Silicon Metal GPU.
- **Universal Web Automation**: Native support for **WebMCP** (`navigator.modelContext`, `document.modelContext`, `<form toolname="...">`) and standard visual DOM web apps (inputs, deep table row scanning, coordinate clicks).
- **Zero Hardcoded Intents**: Open-ended natural language goals (e.g. *"compare 2 products"*, *"see if available"*, *"export report"*) are dynamically decomposed by local **Ollama** (`llama3.1:8b`) without maintaining rigid intent tables.
- **Live Affordance Perception**: Dynamic in-memory discovery of tools, navigation links, and screen buttons in <3ms via Chrome DevTools Protocol, eliminating stale disk caches.
- **Automated Execution Reports**: Produces transparent Markdown audit trails (`execution_report.md`) with latency benchmarks, masked credentials, and high-resolution visual proof (`final_execution_state.png`).

---

## 🧪 Tested With PostQode Nexus

Laya Browser Agent is battle-tested against **[PostQode Nexus](https://github.com/Sreenuraj/postqode-nexus)**, a full-stack enterprise e-commerce platform built with Next.js 15, Spring Boot 3, and PostgreSQL.

If you want to experiment or run end-to-end multi-role scenarios locally:
1. Clone and launch PostQode Nexus from [https://github.com/Sreenuraj/postqode-nexus](https://github.com/Sreenuraj/postqode-nexus).
2. Point Laya Browser Agent at `http://localhost:3000` to execute shopping, order cancellation, admin approvals, and command center tasks.

---

## 🚀 Quick Start

### 1. Requirements
- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.11 or 3.12
- [Ollama](https://ollama.ai) installed with `llama3.1:8b` pulled:
  ```bash
  ollama run llama3.1:8b
  ```

### 2. Setup (First time only)
```bash
./setup.sh
```
This script configures your virtual environment (`.venv`), installs MLX and Playwright dependencies, downloads Chromium, and pre-caches the Laya MLX model.

### 3. Launch the Agent

**Interactive Terminal Mode:**
```bash
./run.sh
```
Prompts for Target URL, task, and Headed/Headless preference.

**Direct CLI Execution (Headed - Visible Browser):**
```bash
./run.sh --url "http://localhost:3000" --task "Login as user (user/User@123), search for Shoes, buy 1 unit and logout" --headed
```

**Direct CLI Execution (Headless - Background / CI):**
```bash
./run.sh --url "http://localhost:3000" --task "Login as admin (admin/Admin@123), go to /orders and approve all pending orders" --headless
```

---

## 🛠 Command-Line Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--url <URL>` | Prompted | Target web application URL |
| `--task <GOAL>` | Prompted | Natural language goal to execute |
| `--headed` | Enabled | Launch visible Chromium browser window |
| `--headless` | Disabled | Run browser silently in background |
| `--model <HF_ID>` | `aac6fef/laya-mlx` | Hugging Face / MLX model identifier |
| `--ollama-url <URL>` | `http://localhost:11434` | Local Ollama endpoint |
| `--ollama-model <M>` | `llama3.1:8b` | Local LLM used for parameter extraction |
| `--report` / `--no-report` | `--report` | Generate Markdown audit trail with visual proof |
| `--report-dir <DIR>` | `reports` | Directory storing execution reports and screenshots |
| `--report-file <PATH>` | `reports/execution_report.md` | Custom path for execution report output |

---

## 📚 Documentation

Detailed documentation is available in the [`doc/`](doc/) directory:
- [**Technical Architecture Specification** (`doc/architecture.md`)](doc/architecture.md): Deep dive into the 3-tier pipeline, MLX decision policy, zero-hardcoding intent handling, below-the-fold table scanning, and Playwright WebMCP actuation.
- [**Product Overview & Capabilities** (`doc/product.md`)](doc/product.md): Enterprise value proposition, supported protocols, reference testing, and feature breakdown.

---

## 🧪 Running Automated Tests

Run the complete self-check suite:
```bash
.venv/bin/python3 tests/test_self_check.py
```

Run the end-to-end multi-scenario test suite against PostQode Nexus:
```bash
.venv/bin/python3 tests/test_scenarios_suite.py
```
*(Covers user purchasing, user order cancellation, admin approval, admin rejection, and admin command center inspection.)*
