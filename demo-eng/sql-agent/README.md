# Marketing Demo: Agentspace Agent Deployment

## Attribution
The following code is based off of: https://github.com/google/adk-samples/tree/main/python/agents/data-science
Please refer to the original repository for additional background.

---

## Project Structure

Here are the key components of the project:

* `sql-agent/`
    * `deploy.py` - The deployment script.
    * `.env` - The file for setting environment and configuration variables.
    * `sql_agent/`
        * `agent.py` - The main agent file.
        * `prompts_prompts.py` - Contains the prompts used by the main agent.
        * `tools/` - Contains tools used by the main agent.
        * `utils/` - Contains common utilities and helper functions.
        * `sub_agents/`
            * `bigquery/`
                * `agent.py` - Main bigquery sub-agent.
                * `prompts.py` - Prompts for the bigquery sub-agent.
                * `tools.py` - Tools for the bigquery sub-agent.

---

## Set up Local Environment

To set up your local environment, run the following commands:

Stand within the `sql-agent` directory.

```bash
poetry config virtualenvs.in-project true #Optional but helpful for vscode
poetry install
eval $(poetry env activate) #Optional. Alternatively just start a new terminal.
gcloud config configurations list
gcloud config configurations activate <name-of-configuration>
gcloud auth application-default login
```

This will install the necessary requirements (in .toml file):
    * google-adk
    * google-genai
    * python-dotenv
    * numpy
    * pandas
    * immutabledict
    * requests
    * google-cloud-aiplatform[adk,agent-engines]
    * google-cloud-bigquery
    * regex
    * sqlglot
    * absl-py
    * db-dtypes

---

## Set up Cloud Project & Environment

Follow these steps to configure your cloud environment.

### 1. Enable APIs

Ensure the following APIs are enabled for your project:
* Vertex AI API
* Cloud Storage API
* Cloud Logging API
* Cloud Monitoring API
* Cloud Trace API
* BigQuery API


### 2. Create GCS Buckets

* Create a GCS bucket for deployment.
* Create a GCS bucket for the artifact service.


### 3. Edit Agent Configuration (`.env` based off of `.env.example` file)
```Markdown
# Environment variables for Vertex backend config
GOOGLE_GENAI_USE_VERTEXAI=1 # Choose Model Backend: 0 -> ML Dev, 1 -> Vertex
GOOGLE_CLOUD_PROJECT=<project_id>
GOOGLE_CLOUD_LOCATION=<region> #e.g. 'us-central1'

# GCS Buckets
GOOGLE_CLOUD_BUCKET_AGENTDEPLOYMENT=<bucket_used_for_agent_deployment_without_gs://
GOOGLE_CLOUD_BUCKET_ARTIFACTS=<bucket_used_for_artifacts_without_gs://

# Set up BigQuery Agent
BQ_COMPUTE_PROJECT_ID=agentspace-marketing-1371
BQ_DATA_PROJECT_ID=<bq_project_id>
BQ_DATASET_ID=<bq_dataset_id>

# Code Variables
NL2SQL_METHOD="BASELINE" # BASELINE or CHASE # SQLGen method. Sets the method for SQL Generation. Baseline uses Gemini off-the-shelf, whereas CHASE uses CHASE-SQL (https://arxiv.org/abs/2410.01943)
BQML_RAG_CORPUS_NAME='' # Leave this empty as it will be populated automatically # Set up RAG Corpus for BQML Agent

# (Optional) Set up Code Interpreter, if it exists. Else leave empty.
# The full resource name of a pre-existing Code Interpreter extension in Vertex AI. 
# If not provided, a new extension will be created. (e.g., projects/<YOUR_PROJECT_ID>/locations/<YOUR_LOCATION>/extensions/<YOUR_EXTENSION_ID>).
# Check the logs/terminal for the ID of the newly created Code Interpreter Extension and provide the value in your environment variables to avoid 
# creating multiple extensions.
CODE_INTERPRETER_EXTENSION_NAME='' # Either '' or 'projects/{GOOGLE_CLOUD_PROJECT}/locations/us-central1/extensions/{EXTENSION_ID}'

# Models used in Agents
ROOT_AGENT_MODEL='gemini-3-flash-preview'
BIGQUERY_AGENT_MODEL='gemini-3-flash-preview'
BASELINE_NL2SQL_MODEL='gemini-3-flash-preview'
CHASE_NL2SQL_MODEL='gemini-3-flash-preview'

#Used for logging and confirming correct deployment
DEMO_AGENT_DISPLAY_NAME="Cymbal BQ Agent"
AGENT_VERSION=20250816.1

```

---

## Test Agent Locally

To test the agent, stand in a parent directory to where the `agent.py` file is and run:

```bash
adk web
```

---

## Deploy Agent

First, build the wheel from within the `sql-agent` directory:

```bash
poetry build --format=wheel
```

* **To deploy a new agent:**
    ```bash
    poetry run python deploy.py
    ```
* **To update an existing agent:**
    ```bash
    poetry run python deploy.py --update_agent <reasoning_agent_ID_within_Agent_Engine>
    ```

---

## Grab Deployed Agent Resource URL

After deployment, you will get a resource URL.

* **Example**: `projects/1035761864249/locations/us-central1/reasoningEngines/47490106226900992`
    * `1035761864249` is the project number.
    * `us-central1` is the project's region.
    * `47490106226900992` is the specific resource ID.

---

## Create New Agentspace App

Within Vertex AI Search create a new Agentspace app. Grab the created app's ID. Follow [instructions](https://cloud.google.com/agentspace/docs/create-app).

---

## Register the deployed agent with the Agentspace app

Use the [agent_registration_tool](https://github.com/VeerMuchandi/agent_registration_tool) to create the Agentspace app. More details [here](https://docs.google.com/document/d/1c8EWRfJk1s7Mmb7Ak40YdnZoZ1_RnKL0x_5S2lNlIWU/edit?tab=t.0#heading=h.retqmruum57b).

#### 1: Download/clone the tool.
https://github.com/VeerMuchandi/agent_registration_tool

#### 2: Configure the tool.
You will need to set up a `config.json` file with the following variables:

| Key | Description |
| --- | --- |
| `project_id` | Your GCP project ID. |
| `location` | The region of your project. |
| `app_id` | The Vertex AI Search App ID. |
| `adk_deployment_id` | The deployed Agent Engine ID. |
| `ars_display_name` | The name for your agent in Agentspace. |
| `description` | Agent Description for Registry from Config. |
| `tool_description` | Tool Description for Registry from Config. |
| `re_location` | The region where the Agent Engine is deployed. |

### Example `config.json`

```json
 {
    "project_id": "1035761864249",
    "location": "us-central1",    
    "app_id": "cymbal-superstore-as_1755096985565",
    "adk_deployment_id": "5830323134003150848",
    "ars_display_name": "Cymbal BQ Agent",
    "description": "Agent to translate natural language to SQL. Capable of querying Cymbal's sales data.",
    "tool_description": "The Cymbal BQ Agent queries Cymbal's sales data. It convert's users Natural Language queries into SQL and returns the results.",
    "re_location": "us-central1",
    "icon_uri": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/database/default/24px.svg"
 }
```

#### 3. Run it:
Call the `register_agent` action

```bash
python as_registry_client.py --config <relative-location-of-json-config-file.json> register_agent
```


## Optional: Edit Agentspace's "Additional LLM system instructions"
Within Agentspace's App, you may want to edit the "Additional LLM system instructions" to further refine the agent's behavior and persona.
This can be found within the Agentspace app's 'Configuration' tab. Example instructions can be found within `agentspace_additional_llm_instructions.md`.

---

