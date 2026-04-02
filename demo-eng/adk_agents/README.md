# ADK Agents Workspace

This repository is a **UV Workspace** managing a suite of AI marketing agents. It provides a unified development environment where agents coexist as independent packages but leverage shared local utilities.

## 🚀 Quick Start

### 1. Unified Sync
Initialize the workspace and install all dependencies for ALL agents at once:
```bash
uv sync
```

### 2. Local Testing (The "One Stop Shop")
The preferred way to develop and test agents locally is using the ADK Web Developer UI.
```bash
# Run from the adk_agents folder
uv run adk web
```
Access the UI at: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 📂 Architecture: Workspace Pattern

This monorepo follows the **UV Workspace Pattern (PEP 621)**. 

### Core Components
- **`adk_common/`**: Shared DTOs, GCS utilities, and logging patterns. Used by all agents.
- **`deploy.py`**: Standardized deployment script for Vertex AI Reasoning Engines.
- **`pyproject.toml`**: Defines workspace members and shared configurations.

---

## 🤖 Available Agents

Each agent is a self-contained package. Explore their individual directories for detailed technical specs and resiliency patterns.

| Agent | Description | Directory |
| :--- | :--- | :--- |
| **Marketing Orchestrator** | The "Boss" agent. Coordinates sub-agent delegation. | [marketing_orchestrator]
| **Ad Generation Agent** | Specialized 'Asset-First' workflow for images/videos. | [ad_generation_agent]
| **GenMedia Agent** | Central media generation service (Vertex AI Image/Video). | [genmedia_agent]
| **Retrieve Asset Agent** | Identifies and retrieves creative assets. | [retrieve_asset_agent]
| **Marketing Plan Agent** | Specialized in high-level campaign planning. | [marketing_plan_agent]

> [!TIP]
> Each agent folder contains its own `README.md` with specific environment variables and configuration details.

---

## 📦 Deployment

Deploy agents as **Vertex AI Reasoning Engines** using the centralized deployment script. This script builds wheels for the agent and all its local workspace dependencies automatically.

### Command Example
```bash
uv run python deploy.py --config_file <agent_folder>/deployment_config/prod.json
```

### Flags
- `-c, --config_file`: Path to the agent's JSON deployment config.
- `-u, --update_agent`: (Optional) Update an existing engine ID instead of creating a new one.

---

## 🛠️ Development Workflow

### Adding Dependencies
To add a package to a specific agent without affecting others:
```bash
uv add <package_name> --package <agent_name>
```

### VS Code Setup
1. Run `uv sync`.
2. Select the interpreter at `.venv/bin/python`.
3. **Go to Definition** cross-links work perfectly because packages are in editable mode.
