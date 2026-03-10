# GenMedia Agent

The **GenMedia Agent** is a specialized sub-agent responsible for all media generation and editing tasks within the workspace. It handles Image Generation, Video Generation, Audio/TTS, and Storyboard creation.

## 🏗️ Architecture

This agent is typically orchestrator-driven but can be run standalone for testing.

### Sub-Agent Protocol
It relies on the `adk_common` library and expects configuration to be loaded into the environment (either via its own `.env` or the parent process).

## ⚙️ Configuration (Environment Variables)

The following environment variables are required for this agent to function.

### Core Model Configuration
| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_GENMEDIA` | Main LLM used by this agent's logic. | `gemini-2.5-flash` | `gemini-2.5-flash`, `gemini-2.5-pro` |
| `IMAGE_GENERATION_MODEL` | Imagen model for creating images. | `imagen-4.0-ultra-generate-001` | `imagen-4.0-generate-001`, `imagen-4.0-ultra-generate-001` |
| `IMAGE_GENERATION_MODEL` | Model for generating images. | `gemini-3.1-flash-image-preview` | `imagen-3.0-generate-001`, `gemini-2.5-flash-image` |
| `VIDEO_GENERATION_MODEL` | Veo model for video generation. | `veo-3.1-generate-preview` | `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview` |
| `AUDIO_TTS_GENERATION_MODEL` | Text-to-Speech model. | `gemini-2.5-pro-tts` | `gemini-2.5-pro-tts` |
| `AUDIO_LYRIA_GENERATION_MODEL` | Music generation mode. | `lyria-002` | `lyria-002` |

### Generation Parameters
| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `IMAGE_DEFAULT_ASPECT_RATIO` | Default aspect ratio for new images. | `9:16` | `1:1`, `3:4`, `4:3`, `9:16`, `16:9` |
| `VIDEO_DEFAULT_ASPECT_RATIO` | Default aspect ratio for new videos. | `9:16` | `9:16`, `16:9` |
| `VIDEO_DEFAULT_RESOLUTION` | Resolution (720p/1080p). | `1080p` | `720p`, `1080p` |
| `VIDEO_DEFAULT_DURATION` | Video duration in seconds. | `4` | `4`, `6`, `8` |
| `MAX_NUMBER_OF_IMAGES` | Images to generate per prompt. | `2` | Integer (1-4) |
| `NUMBER_OF_STORYBOARD_SCENES` | Scenes in a generated storyboard. | `3` | Integer (2-6) |

### Resilience & Concurrency
*(Advanced tuning for quotas and retries)*

| Variable | Description | Default | Options |
| :--- | :--- | :--- | :--- |
| `IMAGE_GENERATION_TENACITY_ATTEMPTS` | Retries for image generation. | `3` | Integer |
| `IMAGE_GENERATION_CONCURRENCY_LIMIT` | Max parallel image requests. | `5` | Integer |
| `VIDEO_GENERATION_TENACITY_ATTEMPTS` | Retries for video generation. | `3` | Integer |
| `VIDEO_GENERATION_CONCURRENCY_LIMIT` | Max parallel video requests. | `3` | Integer |
| `VIDEO_GENERATION_STATUS_POLL_SECONDS` | Polling interval for video status. | `3` | Integer |

## 🏃 Usage

### Run as Module
```bash
uv run python -m genmedia_agent.agent
```

### Deployment
This agent uses the workspace-wide `deploy.py` script.
 
#### 1. Deploy New Agent
```bash
# From adk_agents root
uv run python deploy.py --config_file genmedia_agent/deployment_config/prod-3.20251118.1.json
```
 
#### 2. Update Existing Agent
1.  Ensure `agent_engine_id_to_update` is set in your config file.
2.  Run with `-u`:
```bash
# From adk_agents root
uv run python deploy.py --config_file genmedia_agent/deployment_config/prod-3.20251118.1.json -u
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
