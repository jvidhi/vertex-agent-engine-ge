# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deployment script."""

import glob
import os
import sys
import json
import copy

import vertexai
from absl import app, flags
from dotenv import load_dotenv

load_dotenv()

from google.adk.artifacts import GcsArtifactService
from google.api_core import exceptions as google_exceptions
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

FLAGS = flags.FLAGS
flags.DEFINE_string("update_agent", None, "Agent Id to Update")

_env_vars = {}


def _get_env_vars() -> dict[str, str]:
    if not _env_vars:
        load_dotenv()
        env_var_keys = [
            "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_CLOUD_BUCKET_AGENTDEPLOYMENT",
            "GOOGLE_CLOUD_BUCKET_ARTIFACTS",
            "NL2SQL_METHOD",
            "BQ_COMPUTE_PROJECT_ID",
            "BQ_DATA_PROJECT_ID",
            "BQ_DATASET_ID",
            "BQML_RAG_CORPUS_NAME",
            "CODE_INTERPRETER_EXTENSION_NAME",
            "ROOT_AGENT_MODEL",
            "BIGQUERY_AGENT_MODEL",
            "BASELINE_NL2SQL_MODEL",
            "CHASE_NL2SQL_MODEL",
            "DEMO_AGENT_DISPLAY_NAME",
            "AGENT_VERSION",
        ]
        
        for key in env_var_keys:
            if value := os.environ.get(key):
                _env_vars[key] = value

        print(f"env vars: {json.dumps(_env_vars)}")

    return _env_vars


def _get_artifact_service():
    import traceback

    try:
        artifactService = GcsArtifactService(
            bucket_name=_get_env_vars()["GOOGLE_CLOUD_BUCKET_ARTIFACTS"]
        )
        print(
            f"Artifact Service of Type: {type(artifactService)}. Created For Bucket: {artifactService.bucket_name}"
        )
        print(f"Current Stack: {traceback.extract_stack()}")
        return artifactService
    except Exception as e:
        print(f"Failed to return GcsArtifactService with error: {e}")
        raise

def _find_latest_whl(directory_path):
    """
    Finds the path to the latest .whl file in the specified directory.

    Args:
        directory_path (str): The path to the directory to search.

    Returns:
        str or None: The full path to the latest .whl file, or None if no .whl files are found.
    """
    whl_files = glob.glob(os.path.join(directory_path, "*.whl"))

    if not whl_files:
        return None  # No .whl files found

    # Sort files by creation time in descending order
    latest_whl = max(whl_files, key=os.path.getctime)
    return latest_whl


def _create(adk_app, root_agent, latest_wheel_file) -> None:
    print("\nAttempting to create agent")
    
    deep_copy_env_vars = copy.deepcopy(_get_env_vars())
    deep_copy_env_vars.pop("GOOGLE_CLOUD_PROJECT")
    deep_copy_env_vars.pop("GOOGLE_CLOUD_LOCATION")
    deep_copy_env_vars.pop("GOOGLE_GENAI_USE_VERTEXAI")
    
    remote_agent = agent_engines.create(
        adk_app,
        requirements=[latest_wheel_file],
        extra_packages=[latest_wheel_file],
        env_vars=deep_copy_env_vars,
        display_name=deep_copy_env_vars["DEMO_AGENT_DISPLAY_NAME"],
        description=f"{root_agent.description} [Version: {deep_copy_env_vars['AGENT_VERSION']}].",
    )
    print(
        f"\nSuccessfully created agent: {remote_agent.resource_name}. Version: {deep_copy_env_vars["AGENT_VERSION"]}"
    )


def _update(adk_app, root_agent, latest_wheel_file, agent_id_to_update) -> None:
    if not agent_id_to_update:
        print(f"Failed to update Agent, specify agent to update")
        raise RuntimeError("Failed to update Agent, no agent specified")

    print(f"\nAttempting to update agent: {agent_id_to_update}")

    existing_agent = agent_engines.AgentEngine(agent_id_to_update)
    if not existing_agent:
        print(f"No agent returned with Id: {agent_id_to_update}")
        raise RuntimeError(f"No agent returned with Id: {agent_id_to_update}")
    
    deep_copy_env_vars = copy.deepcopy(_get_env_vars())
    deep_copy_env_vars.pop("GOOGLE_CLOUD_PROJECT")
    deep_copy_env_vars.pop("GOOGLE_CLOUD_LOCATION")
    deep_copy_env_vars.pop("GOOGLE_GENAI_USE_VERTEXAI")
    
    remote_agent = existing_agent.update(
        agent_engine=adk_app,
        requirements=[latest_wheel_file],
        extra_packages=[latest_wheel_file],
        env_vars=deep_copy_env_vars,
        display_name=deep_copy_env_vars["DEMO_AGENT_DISPLAY_NAME"],
        description=f"{root_agent.description} [Version: {deep_copy_env_vars['AGENT_VERSION']}].",
    )
    print(f"\nSuccessfully updated agent: {remote_agent.resource_name}")


def main(argv: list[str]) -> None:  # pylint: disable=unused-argument
    try:
        vertexai.init(
            project=_get_env_vars()["GOOGLE_CLOUD_PROJECT"],
            location=_get_env_vars()["GOOGLE_CLOUD_LOCATION"],
            staging_bucket=f"gs://{_get_env_vars()["GOOGLE_CLOUD_BUCKET_AGENTDEPLOYMENT"]}",
        )
        
        # Import here so that it does not try to initialize config before load_dotenv called
        from sql_agent.agent import root_agent

        """Creates and deploys the agent."""
        adk_app = AdkApp(
            agent=root_agent,
            enable_tracing=False,
            artifact_service_builder=_get_artifact_service,
            env_vars=_get_env_vars(),
        )

        adk_app.set_up()

        print(
            f"AdkApp has Artifact Service Builder: {adk_app._tmpl_attrs.get("artifact_service_builder")} - and Artifact Service: {adk_app._tmpl_attrs.get("artifact_service")}"
        )
        
        latest_wheel_file = _find_latest_whl("dist/")
        
        if not latest_wheel_file:
            print(
                "Agent wheel file not found at directory: `/dist`; run `poetry build`"
            )
            raise FileNotFoundError("Agent wheel file not found at directory: `/dist`; run `poetry build`")
        else:
            print(f"Found latest wheel file: {latest_wheel_file}")

        agent_id_to_update = None
        should_update_agent = False
        if FLAGS.update_agent:
            agent_id_to_update = FLAGS.update_agent
            should_update_agent = True
            
        if should_update_agent:
            _update(adk_app, root_agent, latest_wheel_file, agent_id_to_update)
        else:
            _create(adk_app, root_agent, latest_wheel_file)

    except google_exceptions.Forbidden as e:
        print(
            "\nPermission Error: Ensure the service account/user has necessary "
            "permissions (e.g., Storage Admin, Vertex AI User, Service Account User on runtime SA)."
            f"\nDetails: {e}",
            file=sys.stderr,
        )
        raise e
    except google_exceptions.NotFound as e:
        print(f"\nResource Not Found Error: {e}", file=sys.stderr)
        raise e
    except google_exceptions.GoogleAPICallError as e:
        print(f"\nGoogle API Call Error: {e}", file=sys.stderr)
        raise e
    except FileNotFoundError as e:
        print(f"\nFile Error: {e}", file=sys.stderr)
        print(
            "Please ensure the agent wheel file exists and you have run the build script.",
            file=sys.stderr,
        )
        raise e
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        raise e


if __name__ == "__main__":

    app.run(main)
