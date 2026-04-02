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

from __future__ import annotations
from google.adk.agents import BaseAgent

"""Deployment script using UV Workspaces."""


import copy
import json
import os
import pathlib
import sys
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

# --- MONKEYPATCH START ---
# Fix for "Message type ... has no field named effectiveIdentity" error
# caused by mismatch between Vertex AI API response and local protobuf definition.
import google.protobuf.json_format
_original_parse = google.protobuf.json_format.Parse

def _patched_parse(text, message, ignore_unknown_fields=False, descriptor_pool=None):
    return _original_parse(text, message, ignore_unknown_fields=True, descriptor_pool=descriptor_pool)

google.protobuf.json_format.Parse = _patched_parse
print("Applied monkeypatch to google.protobuf.json_format.Parse to ignore unknown fields.")
# --- MONKEYPATCH END ---


import vertexai
from absl import app, flags
from adk_common.dtos.agent_deploy_config import AgentDeployConfig
# from adk_common.utils import utils_gcs  # Moved to lazy import in main
# from google.adk.agents import BaseAgent # Moved to point of use
# from google.adk.artifacts import GcsArtifactService # Moved to point of use
from google.api_core import exceptions as google_exceptions
from google.cloud import storage
from pydantic import ValidationError
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

FLAGS = flags.FLAGS
flags.DEFINE_bool(
    "update_agent",  # Flag name
    False,  # Default value
    "Should update Agent Engine Agent True/False",  # Help string
    short_name="u",
)

flags.DEFINE_string(
    "config_file",  # Flag name
    None,  # Default value
    "The relative or absolute path to the agent's JSON config file.",  # Help string
    short_name="c",
)

flags.mark_flag_as_required("config_file")


def get_agent_to_deploy(config: AgentDeployConfig) -> "BaseAgent":
    """Dynamically determines and returns the agent to be deployed using ImportLib."""
    import importlib
    import sys
    from google.adk.agents import BaseAgent

    agent_root = str(Path(config.whl_file_path).parent.parent.absolute())
    if agent_root not in sys.path:
        sys.path.insert(0, agent_root)
        print(f"Added {agent_root} to sys.path")

    # Ensure module and variable are provided
    if not config.agent_module or not config.agent_variable:
        raise ValueError(
            "Configuration Error: 'agent_module' and 'agent_variable' are required in the deployment config.\n"
            "Please update your JSON config file to include these fields.\n"
            "Example:\n"
            "  \"agent_module\": \"marketing_orchestrator.agent\",\n"
            "  \"agent_variable\": \"marketing_orchestrator\""
        )

    try:
        module = importlib.import_module(config.agent_module)
        agent = getattr(module, config.agent_variable)
        return agent
    except ImportError as e:
        raise ValueError(f"Could not import module '{config.agent_module}': {e}")
    except AttributeError as e:
        raise ValueError(f"Could not find attribute '{config.agent_variable}' in module '{config.agent_module}': {e}")


def find_workspace_root(start_path: Path) -> Path:
    """Finds the UV workspace root by searching for a pyproject.toml with [tool.uv.workspace]."""
    for parent in [start_path] + list(start_path.parents):
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
                if "tool" in data and "uv" in data["tool"] and "workspace" in data["tool"]["uv"]:
                    return parent
    return start_path # Fallback to start_path if not found


def get_workspace_members(workspace_root: Path) -> dict[str, Path]:
    """Returns a mapping of member names to their absolute paths."""
    pyproject_path = workspace_root / "pyproject.toml"
    members_map = {}
    if pyproject_path.is_file():
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
            for member in members:
                member_path = workspace_root / member
                # Read the member's pyproject to get its actual name
                member_pyproject = member_path / "pyproject.toml"
                if member_pyproject.is_file():
                    with open(member_pyproject, "rb") as f_m:
                        m_data = tomllib.load(f_m)
                        m_name = m_data.get("project", {}).get("name")
                        if m_name:
                            members_map[m_name] = member_path
    return members_map


def get_local_dependencies(agent_root: Path, workspace_members: dict[str, Path]) -> list[Path]:
    """Finds which workspace members are dependencies of the given agent."""
    pyproject_path = agent_root / "pyproject.toml"
    local_deps = []
    if pyproject_path.is_file():
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            deps = data.get("project", {}).get("dependencies", [])
            for dep in deps:
                # Basic parsing: 'adk_common==1.0' -> 'adk_common'
                dep_name = dep.split("==")[0].split(">")[0].split("<")[0].split(" ")[0].strip()
                if dep_name in workspace_members:
                    local_deps.append(workspace_members[dep_name])
    return local_deps


def build_package_wheel(package_root: Path) -> str:
    """Builds a wheel for a package and returns the path to the .whl file."""
    print(f"Building wheel for: {package_root}")
    try:
        # Clear dist directory first to avoid picking up old wheels
        dist_dir = package_root / "dist"
        if dist_dir.exists():
            import shutil
            shutil.rmtree(dist_dir)
            
        subprocess.run(["uv", "build", "--out-dir", "dist"], cwd=package_root, check=True)
        # Find the .whl file in the dist directory
        wheels = list(dist_dir.glob("*.whl"))
        if not wheels:
            raise RuntimeError(f"No wheel found in {dist_dir} after build.")
        # Return the most recent one
        latest_wheel = max(wheels, key=os.path.getmtime)
        return str(latest_wheel)
    except subprocess.CalledProcessError as e:
        print(f"Error building wheel in {package_root}: {e}")
        raise RuntimeError(f"Failed to build wheel for {package_root}") from e



def get_version_from_toml(config: AgentDeployConfig | None = None) -> str:
    # Set a default value in case of an error
    version = "unknown"

    # Define the path to pyproject.toml
    # Default: relative to the current working directory
    pyproject_path = Path.cwd() / "pyproject.toml"
    
    # If config is provided, check the agent directory
    if config and config.whl_file_path:
        try:
            # Assumes whl path structure: dist/name-version.whl -> parent.parent is agent root
            agent_root = Path(config.whl_file_path).parent.parent
            candidate_path = agent_root / "pyproject.toml"
            if candidate_path.is_file():
                pyproject_path = candidate_path
                print(f"Using pyproject.toml from agent directory: {pyproject_path}")
        except Exception as e:
            print(f"Could not determine pyproject.toml from wheel path: {e}")

    # Check if the file exists and is a file
    if pyproject_path.is_file():
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        try:
            # Standard PEP 621 project version (UV Standard)
            version = data["project"]["version"]
        except KeyError:
            print("Version key not found in [project] section of pyproject.toml.")
    else:
        print(f"pyproject.toml not found at {pyproject_path}")

    print(f"Extracted version: {version}")
    return version


def run_version_safety_checks(deployment_config: AgentDeployConfig):
    toml_version = get_version_from_toml(deployment_config)
    deploy_config_version = deployment_config.env_vars.get("AGENT_VERSION", "unknown")
    if toml_version != deploy_config_version:
        raise RuntimeError(f"Version in pyproject.toml ({toml_version}) does not match deployment config ({deploy_config_version}).")
        
    if not toml_version in deployment_config.whl_file_path:
        # Check if we need to rebuild
        raise RuntimeError(f"Wheel file {deployment_config.whl_file_path} might not match current version {toml_version}. Please rebuild.")

  
def load_agent_config_from_json(file_path: str) -> AgentDeployConfig:
    config_path = pathlib.Path(file_path)

    try:
        json_data = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        print(f"Error: Configuration file not found at '{file_path}'")
        raise e
    except IOError as e:
        print(f"Error: Could not read file at '{file_path}'. Check permissions.")
        raise e

    try:
        data_dict = json.loads(json_data)
        print(f"Loaded JSON data: {json.dumps(data_dict)}")
        config = AgentDeployConfig(**data_dict)
        
        run_version_safety_checks(config)
        
        return config
    except json.JSONDecodeError as e:
        print(f"Error: File '{file_path}' is not a well-formatted JSON file.")
        raise e
    except ValidationError as e:
        print(
            f"Error: File '{file_path}' content does not match the "
            f"AgentDeployConfig schema."
        )
        raise e


def get_artifact_service():
    import traceback
    from adk_common.utils.constants import get_required_env_var
    from google.adk.artifacts import GcsArtifactService

    try:
        artifactService = GcsArtifactService(
            bucket_name=get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")
        )
        print(
            f"Artifact Service of Type: {type(artifactService)}. Created For Bucket: {artifactService.bucket_name}"
        )
        return artifactService
    except Exception as e:
        print(f"Failed to return GcsArtifactService with error: {e}")
        raise


# def get_session_service():
#     import traceback
#     from adk_common.utils.constants import get_required_env_var
    
#     try:
#         project = get_required_env_var("GOOGLE_CLOUD_PROJECT")
#         location = get_required_env_var("GOOGLE_CLOUD_LOCATION")
        
#         sessionService = VertexAiSessionService(
#             project=project,
#             location=location
#         )
#         print(
#             f"Session Service of Type: {type(sessionService)}. Created For Project: {project}, Location: {location}"
#         )
#         print(f"Current Stack: {traceback.extract_stack()}")
#         return sessionService
#     except Exception as e:
#         print(f"Failed to return VertexAiSessionService with error: {e}")
#         raise


def build_agent_and_deps(config: AgentDeployConfig) -> list[str]:
    """Builds the agent wheel and all its local workspace dependencies."""
    agent_root = Path(config.whl_file_path).parent.parent
    workspace_root = find_workspace_root(agent_root)
    workspace_members = get_workspace_members(workspace_root)
    local_deps = get_local_dependencies(agent_root, workspace_members)
    
    print(f"\nDiscovered {len(local_deps)} local workspace dependencies.")
    
    wheels = []
    # Build dependencies first
    for dep_root in local_deps:
        wheels.append(build_package_wheel(dep_root))
    
    # Build the agent itself
    wheels.append(build_package_wheel(agent_root))
    
    return wheels


def deploy_new_agent(config: AgentDeployConfig, deployment_env_vars: dict[str, str], wheels: list[str]) -> None:
    
    agent_to_deploy = get_agent_to_deploy(config)
    print(f"\nAttempting to deploy agent: {config.agent}.")
    
    vertexai.init(
        project=config.env_vars["GOOGLE_CLOUD_PROJECT"],
        location=config.google_cloud_reasoning_engine_location,
        staging_bucket=f"gs://{config.gcs_bucket_deployment_name}",
    )
    
    """Creates and deploys the agent."""
    adk_app = AdkApp(
        agent=agent_to_deploy,
        artifact_service_builder=get_artifact_service,
        # session_service_builder=get_session_service,
        env_vars=deployment_env_vars,
    )

    adk_app.set_up()

    print("\nAttempting to create agent")

    remote_agent = agent_engines.create(
        adk_app,
        requirements=wheels,
        extra_packages=wheels,
        env_vars=deployment_env_vars, # type: ignore
        display_name=config.agent_display_name,
        description=f"{config.agent_description} [Version: {deployment_env_vars.get('AGENT_VERSION', 'unknown')}] [Deployed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}].",
    )
    print(
        f"\nSuccessfully created agent: {remote_agent.resource_name}. Version: {deployment_env_vars.get('AGENT_VERSION', 'unknown')}"
    )


def update_existing_agent(config: AgentDeployConfig, deployment_env_vars: dict[str, str], wheels: list[str]) -> None:

    if not config.agent_engine_id_to_update:
        raise ValueError(
            f"\nError: Cannot update agent without `agent_engine_id_to_update` set in config file."
        )
    
    agent_to_deploy = get_agent_to_deploy(config)
    print(f"\nAttempting to update agent: {config.agent}. With ID: {config.agent_engine_id_to_update}.")

    vertexai.init(
        project=config.env_vars["GOOGLE_CLOUD_PROJECT"],
        location=config.google_cloud_reasoning_engine_location,
        staging_bucket=f"gs://{config.gcs_bucket_deployment_name}",
    )

    """Creates and deploys the agent."""
    adk_app = AdkApp(
        agent=agent_to_deploy,
        artifact_service_builder=get_artifact_service,
        env_vars=deployment_env_vars,
    )

    adk_app.set_up()

    existing_agent = agent_engines.AgentEngine(config.agent_engine_id_to_update)
    if not existing_agent:
        raise RuntimeError(
            f"No agent returned with Id: {config.agent_engine_id_to_update}"
        )

    remote_agent = existing_agent.update(
        agent_engine=adk_app,
        requirements=wheels,
        extra_packages=wheels,
        env_vars=deployment_env_vars, # type: ignore
        display_name=config.agent_display_name,
        description=f"{config.agent_description} [Version: {deployment_env_vars.get('AGENT_VERSION', 'unknown')}] [Deployed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}].",
    )
    print(f"\nSuccessfully updated agent: {remote_agent.resource_name}")


def main(argv: list[str]) -> None:  # pylint: disable=unused-argument
    del argv
    file_path = FLAGS.config_file

    try:
        print(f"Attempting to load config from: {file_path}")
        config = load_agent_config_from_json(file_path)

        print("\nSuccess! Configuration loaded and validated.")
        print("--- Config Details ---")
        print(config.model_dump_json(indent=2))
        print("------------------------")

        print("\nInjecting environment variables from deployment config...")
        for key, value in config.env_vars.items():
            os.environ[key] = value
        print("Environment variables injected.")

    except (FileNotFoundError, IOError, json.JSONDecodeError, ValidationError):
        print("\nError: Failed to load configuration.")
        raise
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        raise

    try:
        from adk_common.utils import utils_gcs
        # Ensure we have the bucket
        bucket: storage.Bucket = utils_gcs.create_bucket_from_spec(
            config.gcs_bucket_deployment_name, 
            config.gcs_bucket_deployment_location,
            project=config.env_vars["GOOGLE_CLOUD_PROJECT"]
        )
        
        deployment_bucket_uri = f"gs://{bucket.name}"
        
        vertexai.init(
            project=config.env_vars["GOOGLE_CLOUD_PROJECT"],
            location=config.google_cloud_reasoning_engine_location,
            staging_bucket=deployment_bucket_uri,
        )

        # Build the agent and all its local dependencies
        wheels = build_agent_and_deps(config)

        deployed_env_vars = copy.deepcopy(config.env_vars)
        deployed_env_vars.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
        deployed_env_vars.pop("GOOGLE_CLOUD_PROJECT", None)
        deployed_env_vars.pop("GOOGLE_CLOUD_LOCATION", None)

        if FLAGS.update_agent:
            update_existing_agent(config, deployed_env_vars, wheels)
        else:
            deploy_new_agent(config, deployed_env_vars, wheels)

    except google_exceptions.Forbidden as e:
        print(
            "\nPermission Error: Ensure the service account/user has necessary "
            "permissions (e.g., Storage Admin, Vertex AI User, Service Account User on runtime SA)."
            f"\nDetails: {e}",
            file=sys.stderr,
        )
        raise
    except google_exceptions.NotFound as e:
        print(f"\nResource Not Found Error: {e}", file=sys.stderr)
        raise
    except google_exceptions.GoogleAPICallError as e:
        print(f"\nGoogle API Call Error: {e}", file=sys.stderr)
        raise
    except FileNotFoundError as e:
        print(f"\nFile Error: {e}", file=sys.stderr)
        print(
            "Please ensure the agent wheel file exists and you have run the build script.",
            file=sys.stderr,
        )
        raise
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    app.run(main)
