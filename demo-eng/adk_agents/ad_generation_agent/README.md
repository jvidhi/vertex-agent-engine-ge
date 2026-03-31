# Ad Generation Agent

## Description
The Ad Generation Agent helps marketing teams generate short-form video ads grounded in their product catalog. It selects products from BigQuery, generates storylines, and produces high-fidelity videos using Vertex AI (Veo, Imagen, and Lyria).

## 🏗️ Architecture & Modes

This agent operates in two modes within the **UV Workspace**:
1. **Standalone**: Runs as a full agent with its own UI via `adk web`.
2. **Sub-Agent**: Imported as a library by `marketing_orchestrator`.

---

## 📂 Project Directory Structure

```
ad_generation_agent/
├── ad_generation_agent/          # Core package
│   ├── func_tools/               # Specialized generation tools
│   │   ├── combine_video.py      # Combines video segments with audio
│   │   ├── evaluate_ad.py        # High-level ad quality assurance
│   │   ├── generate_asset_sheet.py # Creates visual style guides
│   │   ├── generate_audio.py     # TTS and background music gen
│   │   ├── generate_display_ad.py # Visual ad asset generation
│   │   ├── generate_image.py     # Storyline-to-image generation
│   │   ├── generate_video.py     # Image-to-video generation (Veo)
│   │   ├── retrieve_generated_assets.py # GCS asset retrieval
│   │   └── select_product.py     # BigQuery product selection
│   ├── utils/                    # Shared utilities and logic
│   │   ├── ad_generation_constants.py
│   │   ├── backup_media_configs.py # Fallback media URLs
│   │   ├── creative.py           # Creative brief processing
│   │   ├── eval_result.py        # Evaluation data models
│   │   ├── evaluate_media.py     # Binary media evaluation logic
│   │   ├── evaluation_prompts.py  # Prompts for the evaluation model
│   │   ├── gemini_utils.py       # Core Gemini API interaction logic
│   │   ├── scene.py              # Scene description models
│   │   └── storytelling.py       # Storytelling prompt fragments
│   ├── agent.py                  # Main orchestration & tool registration
│   └── prompt.md                 # System instructions
├── deployment_config/            # Versioned deployment JSONs
├── pyproject.toml                # Project metadata and deps
└── README.md                     # This file
```

## 🚀 Getting Started

### Prerequisites
- [uv](https://github.com/astral-sh/uv) (v0.4.0+)
- Python 3.11+
- Google Cloud SDK (`gcloud`)

### Setting up some test data in GCS

### 1. Unified Sync
Run these commands from the **workspace root** (`adk_agents`):
```bash
uv sync
gcloud auth application-default login
```

### 2. Infrastructure Setup
Enable the required Google Cloud APIs for your project:
```bash
# Set your active gcloud project
gcloud config set project <YOUR-PROJECT-ID>

# Enable APIs
gcloud services enable \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    cloudresourcemanager.googleapis.com
```

### 3. Environment Configuration
Create a `.env` file in the project root with the required variables (see [Configuration](#-configuration-env-vars)).

> [!IMPORTANT]
> The `DEMO_COMPANY_NAME` you choose must have a corresponding `<brandname>.toml` file in the `brand_configs/` directory (e.g., `brand_configs/vantus.toml`). You must then run the `create_reference_images.py` script for that brand name (see below) to generate reference assets and sync your configurations to GCS.

### 4. Reference Image Generation (Optional)
You can use the standalone script to generate high-quality reference images and automatically upload them (along with your brand configurations) to GCS.

**Example 1: Initialize Brand Assets (Logo & Character)**
This will use Gemini to generate brand-aligned prompts based on your `.toml` config and create a sample logo and character.
```bash
uv run python create_reference_images.py --init
```

**Example 2: Generate a specific product reference**
```bash
uv run python create_reference_images.py \
    --prompt "A luxurious, organic facial cream in a frosted glass jar, surrounded by fresh aloe vera leaves and white flowers, high-end commercial photography style" \
    --filename "organic_cream_luxury"
```

**Example 2: Generate a lifestyle reference with specific aspect ratio**
```bash
uv run python create_reference_images.py \
    --prompt "A woman using a high-end smartphone in a modern, sunlit cafe, cinematic lighting, 8k resolution" \
    --filename "smartphone_lifestyle" \
    --aspect_ratio "16:9"
```

### 5. Local Testing
Launch the ADK Web UI from the **workspace root**:
```bash
uv run adk web
```

### 6. Running Agentic Evaluations
This agent uses the `AgentEvaluator` to deterministically test LLM trajectories and tool routing against deterministic JSON configuration files via a unified script. **All structural evaluation JSONs MUST reside in the singular, top-level `/evals` directory alongside the unifying `test_config.json` file. Do not nest them.**
* **CI/CD Execution**: To automatically discover and run all tests, use PyTest and capture logs:
  ```bash
  uv run pytest test_evals.py > eval_results/pytest_run.log 2>&1
  ```
* **Manual Execution (CLI)**: To run a specific test and capture verbose execution traces locally to a timestamped file in `eval_results/`, execute it as a Python script:
  ```bash
  uv run python test_evals.py evals/storyboard_hierarchy.test.json
  ```

---

## ⚙️ Configuration (Env Vars)

All variables are required either in `.env` (local) or the deployment JSON.

### Core Infrastructure
| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `GOOGLE_CLOUD_PROJECT` | GCP Project ID. | `my-project` |
| `GOOGLE_CLOUD_LOCATION` | Default region for Vertex AI. | `us-central1` |
| `MODELS_CLOUD_LOCATION` | Region for the Gemini client. | `us-central1` |
| `GOOGLE_GENAI_USE_VERTEXAI` | Toggle Vertex AI vs GenAI API. | `true` |
| `GOOGLE_CLOUD_BUCKET_ARTIFACTS` | Bucket for media artifacts. | `my-artifacts` |
| `AGENT_VERSION` | Version tag for state/deployment. | `4.20260219.1` |
| `DEMO_COMPANY_NAME` | Fictitious brand name. | `ACME Corp` |


### Models (LLM)
| Variable | Description | Default |
| :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_ADGEN_ROOT` | Orchestrator model. | `gemini-3-pro-preview` |
| `LLM_GEMINI_MODEL_ADGEN_SUBCALLS` | Sub-task/Storytelling model. | `gemini-3-pro-preview` |
| `LLM_GEMINI_MODEL_EVALUATION` | Media evaluation model. | `gemini-3-pro-preview` |

### Models (Media Generation)
| Variable | Description | Default |
| :--- | :--- | :--- |
| `IMAGE_GENERATION_MODEL` | Imagen model version. | `imagen-3.0-generate-001` |
| `VIDEO_GENERATION_MODEL` | Veo model version. | `veo-3.1-generate-preview` |
| `AUDIO_TTS_GENERATION_MODEL` | Text-to-Speech model. | `gemini-2.5-pro-tts` |
| `AUDIO_LYRIA_GENERATION_MODEL` | Music/SFX model. | `lyria-002` |

### Generation Defaults
| Variable | Description | Default |
| :--- | :--- | :--- |
| `IMAGE_DEFAULT_ASPECT_RATIO` | Default ratio for images. | `9:16` |
| `VIDEO_DEFAULT_ASPECT_RATIO` | Default ratio for videos. | `9:16` |
| `VIDEO_DEFAULT_DURATION` | Target video length (secs). | `4` |
| `AUDIO_TTS_VOICE_NAME` | Default TTS voice. | `Aoede` |
| `RENDER_IMAGES_INLINE` | Render results in chat. | `true` |
| `RENDER_VIDEOS_INLINE` | Render results in chat. | `true` |

### Resiliency & Performance
| Variable | Description | Default |
| :--- | :--- | :--- |
| `IMAGE_GENERATION_TENACITY_ATTEMPTS` | Retries for API calls. | `3` |
| `VIDEO_GENERATION_TENACITY_ATTEMPTS` | Retries for API calls. | `3` |
| `AUDIO_GENERATION_TENACITY_ATTEMPTS` | Retries for API calls. | `3` |
| `IMAGE_GENERATION_EVAL_REATTEMPTS` | Quality re-gens. | `2` |
| `VIDEO_GENERATION_EVAL_REATTEMPTS` | Quality re-gens. | `2` |
| `IMAGE_GENERATION_CONCURRENCY_LIMIT` | Parallel job limit. | `5` |
| `VIDEO_GENERATION_CONCURRENCY_LIMIT` | Parallel job limit. | `3` |
| `VIDEO_GENERATION_RETRY_DELAY_SECONDS` | Delay between retries. | `5` |
| `VIDEO_GENERATION_STATUS_POLL_SECONDS` | Poll frequency. | `5` |

### Fallbacks (Backup Assets)
| Variable | Description |
| :--- | :--- |

---

## 📦 Deployment

Deploy as a **Vertex AI Reasoning Engine** from the workspace root.

```bash
# From workspace root
uv run python deploy.py --config_file ad_generation_agent/deployment_config/prod-<version>.json
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

### 🛡️ Reasoning Engine Permissions

The Reasoning Engine service account requires **Storage Object Admin** permissions (to manage artifact lifecycles, including deletion) as well as **BigQuery read access** (for product catalog lookups).

Run the following commands, replacing `[PROJECT_ID]` with your project ID:

```bash
# Get the Project Number
PROJECT_NUMBER=$(gcloud projects describe [PROJECT_ID] --format='value(projectNumber)')

# Construct the Service Account name
SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

# Grant GCS Permissions
gcloud projects add-iam-policy-binding [PROJECT_ID] \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/storage.objectAdmin"

# Grant BigQuery Permissions
gcloud projects add-iam-policy-binding [PROJECT_ID] \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding [PROJECT_ID] \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/bigquery.jobUser"
```

---

## 🎬 Sample Prompts

Here are a few ways to get started with the Ad Generation Agent. You can paste these directly into the chat:

### 1. The "All-in-One" Request
> "I want to create a 15-second video ad for our new **Organic Aloe Vera Face Cream**. Do all steps automatically and show me the final video. Our brand is **Organic Living**."

### 2. The Step-by-Step Creative Approach
> "Hi! I'm working with **Vantus**. We want to feature the **Vantus Sport Watch**. Let's start by searching for our brand assets and then I'd like to see a few different storyline options before we generate any images."

### 3. Lifestyle & Character Focus
> "Create a high-end commercial for **Allstate**. The ad should feature a young family in their first home, feeling secure and happy. Let's generate a character sheet for the family first to make sure the look is consistent across the scenes."

### 4. Quick Static Asset Generation
> "I just need a quick high-quality display ad for the **Vantus Wireless Earbuds**. Use a 'cyberpunk' aesthetic with neon blue and purple lighting. Include the headline 'Sound of the Future'."

---

## License
This project is licensed under the Apache License, Version 2.0.
