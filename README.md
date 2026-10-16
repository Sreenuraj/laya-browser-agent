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
Or with direct flags:
```bash
./run.sh --url "http://localhost:3000/login" --task "Login as user (user/User@123), search products, buy 1 unit, and logout" --headed
```

## Features
- **$0.00 Cost**: Zero cloud API calls, zero credit card required.
- **Sub-20ms Decision Latency**: Powered by Laya typed decision policy on Apple Silicon MLX GPU.
- **WebMCP Native**: Direct programmatic tool calling on web pages (`page.webmcp.callTool`).
- **Interactive Terminal UI**: Inspired by modern developer terminal tools with live decision telemetry.

See [`doc/plan.md`](doc/plan.md) for the complete engineering architecture and design details.
