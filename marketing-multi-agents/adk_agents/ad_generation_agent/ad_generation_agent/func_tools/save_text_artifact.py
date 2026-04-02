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
"""Tool for saving text artifacts to GCS."""

import datetime
import random
import string
from typing import Dict, Any

from ad_generation_agent.utils import ad_generation_constants
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.utils_logging import Severity, log_function_call, log_message
from google.adk.tools.tool_context import ToolContext

@log_function_call
async def save_text_artifact(
    text: str,
    artifact_type: str,
    tool_context: ToolContext,
    filename_suffix: str = ""
) -> Dict[str, Any]:
    """Saves a text-based artifact (like a storyline or evaluation report) to GCS.

    Args:
        text (str): The text content to save.
        artifact_type (str): The type of artifact (e.g., 'storyline', 'evaluation_report').
        tool_context (ToolContext): The context for artifact management.
        filename_suffix (str, optional): An optional suffix for the filename.

    Returns:
        Dict[str, Any]: A dictionary containing the status and GCS URI of the saved artifact.
    """
    try:
        # Generate filename
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        
        filename = f"{artifact_type}_{timestamp_str}"
        if filename_suffix:
            filename += f"_{filename_suffix}"
        filename += f"_{random_chars}.txt"

        # Create GeneratedMedia object
        generated_media = GeneratedMedia(
            filename=filename,
            mime_type="text/plain",
            media_bytes=text.encode("utf-8")
        )

        # Save to GCS
        session_id = utils_agents.get_or_create_unique_session_id(tool_context)
        gcs_folder = f"{ad_generation_constants.SESSIONS_PREFIX}/{session_id}"
        
        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            asset=generated_media,
            context=tool_context,
            save_in_gcs=True,
            save_in_artifacts=True, # Always save text artifacts as they are small
            gcs_folder=gcs_folder
        )

        log_message(f"Saved text artifact '{filename}' to {generated_media.gcs_uri}", Severity.INFO)
        
        return {
            "status": "success",
            "filename": filename,
            "gcs_uri": generated_media.gcs_uri
        }

    except Exception as e:
        error_msg = f"Error saving text artifact: {str(e)}"
        log_message(error_msg, Severity.ERROR)
        return {
            "status": "failed",
            "detail": error_msg
        }
