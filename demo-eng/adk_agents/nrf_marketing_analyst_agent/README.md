# NRF Marketing Analyst Agent

## Description

The Marketing Analyst Agent is a creative AI assistant designed to help marketing teams find insights across web and enterprise data sources, analyze campaign performance, and generate reports. It acts as a strategic partner, managing campaign data and providing actionable intelligence.

**Deterministic Logic Layer**: This agent follows configurations provided within `data_campaigns.xml`. It focuses on retrieving and presenting existing campaign strategy rather than hallucinating new ones, ensuring brand consistency during high-stakes demonstrations.

## Features
- **Campaign Management**: Parses and manages campaign data from XML sources.
- **Insight Generation**: Uses RAG (Retrieval Augmented Generation) via Vertex AI Search (Data Stores) to find relevant enterprise information.
- **Creative Strategy Execution**: Pairs with generative agents to suggest and present creative assets.
- **State Persistence**: Persists session state (selected campaigns, asset sheets) to GCS to maintain context across interactions.

---

## 📂 Project Directory Structure

```
nrf_marketing_analyst_agent/
├── nrf_marketing_analyst_agent/
│   ├── agent.py                  # Main Agent Definition & Tool Registration.
│   ├── prompt.md                 # System Instruction (Prompt).
│   ├── campaign_utils.py         # Utilities for parsing data_campaigns.xml.
│   ├── data_campaigns.xml        # Source of truth for marketing campaigns.
│   └── generate_display_ad.py    # Tool for generating visual display ads.
├── deployment_config/            # Environment-specific JSON configs.
├── pyproject.toml                # Project metadata and dependencies.
└── README.md                     # This file.
```

## 🚀 Getting Started

### Prerequisites

- [uv](https://github.com/astral-sh/uv) (v0.4.0+)
- Python 3.13+
- Google Cloud SDK (`gcloud`)

### 1. Environment Setup

Run these commands from the **workspace root** (`adk_agents`):

```bash
uv sync
gcloud auth application-default login
```

### 2. Local Testing
Launch the ADK Web UI from the **workspace root**:
```bash
uv run adk web
```
Select `nrf_marketing_analyst_agent` in the dropdown at [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

## ⚙️ Configuration (Env Vars)

Variables required in `.env` or `deployment_config/*.json`.

### Core Infrastructure
| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `GOOGLE_CLOUD_PROJECT` | GCP Project ID. | `my-project` |
| `GOOGLE_CLOUD_LOCATION` | Default execution location. | `global` |
| `AGENT_VERSION` | Explicit version string for state isolation. | `4.20260219.1` |
| `DEMO_COMPANY_NAME` | Brand name used for prompting. | `Vantus` |

### Storage & Data
| Variable | Description | Example |
| :--- | :--- | :--- |
| `MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET` | Bucket for session state and RAG buffering. | `my-analyst-bucket` |
| `CAMPAIGNS_CONFIG_URL` | URL to the `data_campaigns.xml` manifest. | `gs://bucket/data_campaigns.xml` |
| `GOOGLE_CLOUD_BUCKET_ARTIFACTS` | Workspace bucket for generated media. | `my-artifacts` |

### Models & UX Pacing
| Variable | Description | Default |
| :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_MARKETING_ANALYST` | Primary analyst model. | `gemini-2.5-flash` |
| `VIDEO_GENERATION_MODEL` | Model for video previews. | `veo-3.1-generate-preview` |
| `SLEEP_SECONDS_GEN_IMAGE` | Delay to simulate image generation UX. | `3.0` |
| `SLEEP_SECONDS_GEN_VIDEO` | Delay to simulate video generation UX. | `7.0` |
| `RENDER_IMAGES_INLINE` | Render image assets directly in chat. | `true` |
| `RENDER_VIDEOS_INLINE` | Render video assets directly in chat. | `true` |

### State Management (Filenames)
| Variable | Description | Default |
| :--- | :--- | :--- |
| `SELECTED_CAMPAIGN_FILE_NAME` | Campaign selection state prefix. | `selected_campaign_name` |
| `SELECTED_ASSET_SHEET_FILE_NAME` | Asset sheet selection state prefix. | `selected_asset_sheet_name` |
| `SESSION_STATE_FILE_NAME` | Global session tracking prefix. | `session_state` |

---

## 📦 Deployment

Deploy as a **Vertex AI Reasoning Engine** using the root deployment script.

```bash
# From workspace root
uv run python deploy.py --config_file nrf_marketing_analyst_agent/deployment_config/prod-<version>.json
```

### Configuration Fields
- `agent`: `"nrf_marketing_analyst_agent"`
- `agent_module`: `"nrf_marketing_analyst_agent.agent"`
- `agent_variable`: `"root_agent"`

### Updating the Agent Version

When releasing a new version of the agent, you must update the version across codebase configurations:

1. **Update package version**: Modify the `version` field in the agent's `pyproject.toml`.
2. **Update environment**: Change the `AGENT_VERSION` variable in the agent's `.env` file (if used locally).
3. **Create new deployment config**:
   * Duplicate the latest JSON configuration in `deployment_config/`.
   * Rename the new file to reflect the new version (e.g., `<version>-prod.json`).
   * Inside the new JSON file, update any internal references to the version (e.g., `env_vars.AGENT_VERSION`, `agent_display_name`, and `whl_file_path`).
4. **Deploy**: Run the deployment script pointing to the new configuration file.
5. **Update Engine ID (New Deployments Only)**: If you deployed a completely new agent (i.e. not using the `--update_agent` flag, and the JSON lacked a value for `agent_engine_id_to_update`), you must update your new deployment JSON post-deployment. You can find the newly generated reasoning engine ID in the terminal logs, which has the structure `projects/[project]/locations/[location]/reasoningEngines/[agent_engine_id]` (e.g., `projects/39704124777/locations/us-central1/reasoningEngines/8551151518353457152`). Extract **only** the `[agent_engine_id]` (the numeric part at the end) and set this value in the `"agent_engine_id_to_update"` field of your JSON config.

---

## License
This project is licensed under the Apache License, Version 2.0.
