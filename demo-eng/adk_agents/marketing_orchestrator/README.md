# Marketing Demo: Agentspace Agent Deployment

The "Boss" agent that coordinates marketing activities. It acts as the primary interface for the user and delegates tasks to sub-agents (Ad Generation, Marketing Plan, etc.).

## 🏗️ Architecture

This project uses a **UV Workspace**. The `marketing_orchestrator` relies on `adk_common` and other agents as dependencies.

### Project Structure (Key Components)

```
marketing_orchestrator/
├── agent.py                  # The main agent file.
├── README.md                 # This file.
├── pyproject.toml            # Dependencies (PEP 621)
└── .env                      # Local configuration
```

## 🚀 Getting Started

### Prerequisites
-   [uv](https://github.com/astral-sh/uv)
-   Google Cloud SDK (`gcloud`)

### 1. Set up Cloud Project & Environment

Ensure the following APIs are enabled:
* Vertex AI API, Cloud Storage API, Cloud Logging/Monitoring API.

**Create GCS Buckets:**
* One for deployment (staging/code).
* One for artifacts (images/videos).

### 2. Configuration (`.env`)

For local development, create a `.env` file.

```bash
cp .env.example .env
```

#### Core Infrastructure
| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `GOOGLE_CLOUD_PROJECT` | Your Google Cloud project ID. | `my-project-id` |
| `GOOGLE_CLOUD_LOCATION` | Region for deployment. | `us-central1` |
| `MODELS_CLOUD_LOCATION` | Region where models are hosted. | `global` |
| `GOOGLE_CLOUD_BUCKET_ARTIFACTS` | GCS bucket for artifacts. | `my-artifacts-bucket` |
| `GOOGLE_CLOUD_BUCKET_CATALOG` | GCS bucket for catalog images. | `my-catalog-bucket` |
| `AGENT_VERSION` | Explicit version string for runtime verification. | `3.20260119.1` |
| `IS_DEBUG_ON` | Enable debug logging. | `1` (True) or `0` (False) |

#### Model Configurations
*(See: https://cloud.google.com/vertex-ai/generative-ai/docs/models)*

| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_ROOT` | Main LLM for root agent tasks. | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-2.5-flash` |

#### Generation Parameters
| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `DEMO_COMPANY_NAME` | Company name in demo prompts. | `Vantus` | Any string |

### Sub-Agent Configuration

This agent orchestrates specialized sub-agents. For the Orchestrator to function correctly, you **MUST** configure the environment variables required by these sub-agents. They are listed below for convenience.

#### 1. Ad Generation Agent Configs
*(See [`../ad_generation_agent/README.md`](../ad_generation_agent/README.md) for full details)*
 
| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_ADGEN_ROOT` | Main orchestration model. | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `LLM_GEMINI_MODEL_ADGEN_SUBCALLS` | Model for smaller tool calls. | `gemini-2.5-flash` | `gemini-2.5-flash` |
| `LLM_GEMINI_MODEL_EVALUATION` | LLM for evaluating generated content. | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `IMAGE_GENERATION_MODEL` | Imagen model version. | `imagen-4.0-ultra-generate-001` | `imagen-3.0-generate-001`, `imagen-4.0-ultra-generate-001` |
| `VIDEO_GENERATION_MODEL` | Veo model version. | `veo-3.1-generate-preview` | `veo-3.1-generate-preview` |
| `AUDIO_TTS_GENERATION_MODEL` | Text-to-speech model. | `gemini-2.5-pro-tts` | `gemini-2.5-pro-tts` |
| `AUDIO_LYRIA_GENERATION_MODEL` | Audio generation model. | `lyria-002` | `lyria-002` |
| `IMAGE_DEFAULT_ASPECT_RATIO` | Aspect ratio for generated images. | `9:16` | `9:16`, `16:9`, `1:1`, `4:3`, `3:4`|
| `VIDEO_DEFAULT_DURATION` | Duration in seconds. | `4` | `4`, `8` |
| `VIDEO_DEFAULT_RESOLUTION` | Video resolution. | `1080p` | `1080p` |
| `MAX_NUMBER_OF_IMAGES` | Max images per turn. | `2` | `1`-`4` |
| `AUDIO_TTS_VOICE_NAME` | Text-to-speech voice name. | `Aoede` | `Aoede`, `Puck`, `Charon`, `Kore`, `Fenrir` |
 
#### 2. Marketing Analyst Agent Configs
*(See [`../nrf_marketing_analyst_agent/README.md`](../nrf_marketing_analyst_agent/README.md) for full details)*
 
| Variable | Description |
| :--- | :--- |
| `MARKETING_ANALYST_DATASTORE_ID` | Vertex AI Search Datastore ID for RAG. |
| `MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET` | Bucket used for buffering or auxiliary storage. |
| `CAMPAIGNS_CONFIG_URL` | URL to the `data_campaigns.xml` file. |
| `LLM_GEMINI_MODEL_MARKETING_ANALYST` | Main agent model. |
 
#### 3. GenMedia Agent Configs
*(See [`../genmedia_agent/README.md`](../genmedia_agent/README.md) for full details)*
 
| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_GENMEDIA` | Main LLM used by this agent's logic. | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `IMAGE_EDITION_MODEL` | Model for editing/inpainting. | `gemini-3-pro-image-preview` | `gemini-3-pro-image-preview` |
| `NUMBER_OF_STORYBOARD_SCENES` | Scenes in a generated storyboard. | `3` | `3`-`6` |

### 3. Grant Permissions
* Grant `Storage Object Admin` to the service account: `@gcp-sa-aiplatform-re.iam.gserviceaccount.com`.

## 🏃 Usage

### Test Agent Locally

To test the agent locally (from the `adk_agents` root):

```bash
uv run adk web
```

### Run as Module

```bash
uv run python -m marketing_orchestrator.agent
```

## 📦 Deployment

We use a unified deployment script at the root.

### 1. Deploy New Agent

To create a **new** instance of the agent in Vertex AI Reasoning Engine:

```bash
# From adk_agents root
uv run python deploy.py --config_file marketing_orchestrator/deployment_config/prod-3.20251118.1.json
```

### 2. Update Existing Agent

To **update** an already deployed agent (e.g., to deploy new code changes):

1.  Ensure `agent_engine_id_to_update` is set in your config file (e.g., `"123456789..."`).
2.  Run the deploy script with the `--update_agent` (or `-u`) flag:

```bash
# From adk_agents root
uv run python deploy.py --config_file marketing_orchestrator/deployment_config/prod-3.20251118.1.json --update_agent
```

### 2. Deployment Config Example (`deployment_config/prod.json`)

This configuration includes the Orchestrator's settings **PLUS** all required variables for its sub-agents.

```json
{
    "agent": "marketing",
    "deployment_environment": "prod",
    "gcs_bucket_deployment_location": "US",
    "agent_display_name": "Cymbal Marketing Agent",
    "whl_file_path": "marketing_orchestrator/dist/marketing_orchestrator-3.20251118.1-py3-none-any.whl",
    "env_vars": {
        "GOOGLE_CLOUD_PROJECT": "your-project-id",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
        "MODELS_CLOUD_LOCATION": "global",
        "GOOGLE_CLOUD_BUCKET_ARTIFACTS": "your-artifacts-bucket",
        "GOOGLE_CLOUD_BUCKET_CATALOG": "your-catalog-bucket",
        "AGENT_VERSION": "3.20251118.1",
        "IS_DEBUG_ON": "1",
        
        "LLM_GEMINI_MODEL_ROOT": "gemini-2.5-flash",
        
        "LLM_GEMINI_MODEL_ADGEN_ROOT": "gemini-2.5-flash",
        "LLM_GEMINI_MODEL_ADGEN_SUBCALLS": "gemini-2.5-flash",
        "LLM_GEMINI_MODEL_EVALUATION": "gemini-2.5-flash",
        "LLM_GEMINI_MODEL_GENMEDIA": "gemini-2.5-flash",
        "LLM_GEMINI_MODEL_MARKETING_ANALYST": "gemini-2.5-flash",
        
        "IMAGE_GENERATION_MODEL": "imagen-4.0-ultra-generate-001",
        "VIDEO_GENERATION_MODEL": "veo-3.1-generate-preview",
        "STORYBOARD_GENERATION_MODEL": "gemini-3-pro-image-preview",
        "IMAGE_EDITION_MODEL": "gemini-3-pro-image-preview",
        "AUDIO_TTS_GENERATION_MODEL": "gemini-2.5-pro-tts",
        "AUDIO_LYRIA_GENERATION_MODEL": "lyria-002",
        
        "MARKETING_ANALYST_DATASTORE_ID": "projects/...",
        "MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET": "...",
        "CAMPAIGNS_CONFIG_URL": "https://storage.cloud.google.com/...",
        
        "DEMO_COMPANY_NAME": "Vantus"
    }
}
```

## 🔗 Agentspace Registration

After deployment, register the agent with Agentspace using the `agent_registration_tool`.

### Config.json for Registry
```json
{
  "project_id": "project-123",
  "location": "us-central1",
  "app_id": "agentspace-app-123",
  "adk_deployment_id": "1234",
  "ars_display_name": "Cymbal Marketing Agent"
}
```

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
