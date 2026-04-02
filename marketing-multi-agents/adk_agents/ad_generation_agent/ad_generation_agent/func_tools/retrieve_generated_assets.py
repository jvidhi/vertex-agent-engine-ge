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
"""Tool for retrieving the list of generated assets from GCS."""

from typing import Any, Dict

from adk_common.utils import utils_agents
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_gcs import get_files_metadata_from_gcs_bucket
from adk_common.utils.utils_logging import Severity, log_function_call, log_message
from adk_common.utils.utils_agents import store_inline_artifact_metadata
from google.adk.tools.tool_context import ToolContext
from adk_common.dtos.generated_media import GeneratedMedia


from ad_generation_agent.utils import ad_generation_constants

GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")


@log_function_call
async def retrieve_generated_assets(
    tool_context: ToolContext,
    folder_path: str = ""
) -> Dict[str, Any]:
    """Retrieves the list of assets generated in the current session or a specific folder from the source of truth (GCS).

    Use this tool when you need to:
    1. Verify if an asset actually exists (e.g., after an error or before using a URI).
    2. Recover from "File Not Found" errors by checking what files ARE available.
    3. Answer user questions about what has been generated so far.
    4. Validate any URI before passing it to another tool.
    5. Scan specific GCS folders for assets when given a list of external URIs.

    The response from this tool is the SOURCE OF TRUTH. If this tool says a file doesn't exist, it doesn't exist,
    regardless of what your internal context says.

    Args:
        tool_context (ToolContext): The tool context.
        folder_path (str): Optional. A specific GCS folder path (prefix) to list assets from. 
                           If provided, it overrides the default session-based lookup. 
                           Use this to check assets in folders other than the current session's default folder.

    Returns:
        Dict[str, Any]: A dictionary containing the list of assets.
            - "status" (str): "success" or "failed".
            - "assets" (List[Dict]): A list of asset metadata dictionaries.
                Each dict contains: "filename", "uri", "authenticated_url", "mime_type", "last_modified".
    """
    target_prefix = ""
    
    try:
        current_session_id = utils_agents.get_or_create_unique_session_id(tool_context)
        
        if folder_path:
            # Clean up the folder path to ensure it's just the prefix
            # Remove gs:// prefix
            target_prefix = folder_path.replace("gs://", "")
            # Remove https://storage.cloud.google.com/ prefix
            target_prefix = target_prefix.replace("https://storage.cloud.google.com/", "")
            # Remove bucket name if present at start (simple heuristic)
            if target_prefix.startswith(f"{GOOGLE_CLOUD_BUCKET_ARTIFACTS}/"):
                 target_prefix = target_prefix[len(GOOGLE_CLOUD_BUCKET_ARTIFACTS) + 1:]
            
            # Remove leading slashes
            target_prefix = target_prefix.lstrip("/")

            # Check if likely a file path (has extension in last segment) and strip filename if so
            # This handles cases where the agent provides a full file URI but implies "check this folder"
            if "." in target_prefix.split("/")[-1]:
                # List of common asset extensions to check against to be safe
                common_exts = [".png", ".jpg", ".jpeg", ".mp4", ".wav", ".json", ".txt"]
                if any(target_prefix.lower().endswith(ext) for ext in common_exts):
                     # Strip the filename to get parent folder
                     if "/" in target_prefix:
                         target_prefix = target_prefix.rsplit("/", 1)[0]
                     else:
                         # If it's just a filename with no directory structure, valid for root prefix
                         target_prefix = ""
        else:
            target_prefix = f"{ad_generation_constants.SESSIONS_PREFIX}/{current_session_id}"
        
        log_message(f"Retrieving generated assets from prefix: {target_prefix}", Severity.INFO)
        
        # We search within the session folder (or specified folder) in the artifacts bucket
        assets = get_files_metadata_from_gcs_bucket(
            bucket_name=GOOGLE_CLOUD_BUCKET_ARTIFACTS,
            prefix=target_prefix
        )
        
        # Sort by last_modified descending (newest first) for convenience
        assets.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
            
        for asset in assets:
            store_inline_artifact_metadata(tool_context, GeneratedMedia(**asset["asset"]))

        response = {
            "status": "success",
            "folder_searched": target_prefix,
            "count": len(assets),
            "assets": assets
        }
        
        if not assets and target_prefix != f"{ad_generation_constants.SESSIONS_PREFIX}/{current_session_id}":
            response["additional_details"] = f"Please note that the current session's folder is `{ad_generation_constants.SESSIONS_PREFIX}/{current_session_id}`, you determine if you need to call this tool again to retrieve assets from said folder."
        
        log_message(f"Retrieved {len(assets)} assets from {target_prefix}.", Severity.INFO)
        return response

    except Exception as e:
        log_message(f"Error retrieving generated assets: {e}", Severity.ERROR)
        return {
            "status": "failed",
            "detail": f"Failed to retrieve assets from folder {target_prefix or folder_path}. Error: {str(e)}"
        }
