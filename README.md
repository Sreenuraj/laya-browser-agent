# Laya Browser Agent Agent ⚡️

> Zero-cost, sub-second autonomous web agent running on Apple Silicon Metal GPU via Laya MLX and WebMCP.

## Quick Start

### 1. Run Setup (First time only)
```bash
./setup.sh
```
This inspects your machine, configures the Python virtual environment, caches the Laya model, and installs Playwright Chromium.

### 2. Launch the Agent
```bash
./run.sh
```
In interactive mode, you will be prompted for your target URL, task, and browser mode (Headed or Headless).

#### Direct Flag Examples:

**Headed Mode (Visible browser window):**
```bash
./run.sh --url "https://my-app.com" --task "Filter items by status active" --headed
```

**Headless Mode (Background execution):**
```bash
./run.sh --url "https://my-app.com" --task "Export daily report" --headless
```

## Features
- **$0.00 Cost**: Zero cloud API calls, zero credit card required.
- **Universal Application Support**: Works with any web app exposing WebMCP tools (`navigator.modelContext`, `document.modelContext`) or declarative forms (`<form toolname="...">`).
- **Headed & Headless Modes**: Watch the agent execute visually in real time or run silently in the background.
- **Sub-20ms Decision Latency**: Powered by Laya typed decision policy on Apple Silicon MLX GPU.
- **Interactive Terminal UI**: Rich developer UX with live decision telemetry and execution receipts.

See [`doc/plan.md`](doc/plan.md) for the complete engineering architecture and design details.
