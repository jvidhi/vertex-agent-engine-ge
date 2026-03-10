# Marketing Plan Agent

The **Marketing Plan Agent** is responsible for strategic planning, determining audience segments, and crafting campaign strategies based on user input and available data.

## ⚙️ Configuration (Environment Variables)

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_MARKETINGPLAN` | The Gemini model used for strategy generation. | `gemini-2.5-flash` |
| `GOOGLE_CLOUD_PROJECT` | GCP Project ID. | |
| `GOOGLE_CLOUD_LOCATION` | Region for Vertex AI calls. | `us-central1` |

## 🏃 Usage

### Run as Module
```bash
uv run python -m marketing_plan_agent.agent
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
