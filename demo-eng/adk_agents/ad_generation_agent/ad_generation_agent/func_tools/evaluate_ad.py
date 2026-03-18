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

from typing import List, Tuple
from ad_generation_agent.utils.evaluate_media import evaluate_media
from ad_generation_agent.utils.eval_result import EvalResult
from adk_common.utils.utils_logging import Severity, log_message, log_function_call
from adk_common.utils import utils_agents
from google.adk.tools.tool_context import ToolContext
from google.genai import types

@log_function_call
async def evaluate_ad(
    media_url: str,
    mime_type: str,
    prompt: str,
    reference_images: List[str],
    tool_context: ToolContext,
) -> str:
    """Evaluates a generated ad asset (image or video) against specific criteria.

    Args:
        media_url (str): The URL/URI of the media asset to evaluate.
        mime_type (str): The MIME type of the asset (e.g., 'video/mp4', 'image/png').
        prompt (str): A detailed description of what the ad is intended to represent.
        reference_images (List[str]): A list of URIs for reference images.
        tool_context (ToolContext): The context for artifact management.

    Returns:
        str: A string representation of the evaluation result.
    """
    
    log_message(f"Starting ad evaluation for {media_url}", Severity.INFO)
    
    try:
        # Load the media to be evaluated
        media_asset = await utils_agents.load_resource(media_url, tool_context)
        if not media_asset or not media_asset.media_bytes:
            return f"Error: Could not load media from {media_url}"

        # Load reference images
        loaded_reference_images: List[Tuple[types.Part, str]] = []
        for ref_uri in reference_images:
            ref_asset = await utils_agents.load_resource(ref_uri, tool_context)
            if ref_asset and ref_asset.media_bytes and ref_asset.mime_type:
                ref_part = types.Part.from_bytes(data=ref_asset.media_bytes, mime_type=ref_asset.mime_type)
                loaded_reference_images.append((ref_part, f"Reference Image: {ref_uri}"))
            else:
                 log_message(f"Warning: Could not load reference image from {ref_uri}", Severity.WARNING)

        # Basic criteria based on prompt and consistency
        # Detailed physics, consistency, and branding rules are handled modularly by evaluation_prompts.py
        evaluation_criteria = (
            f"Ensure the media accurately represents the following prompt: '{prompt}'"
        )

        result: EvalResult = await evaluate_media(
            media_bytes=media_asset.media_bytes,
            mime_type=mime_type,
            evaluation_criteria=evaluation_criteria,
            reference_images=loaded_reference_images if loaded_reference_images else None,
            allow_collage=False, # Assuming ads are final outputs, not collages, unless specified otherwise
            is_final_ad=(mime_type == "video/mp4") # Use final ad prompt for all video evaluations in this tool
        )

        if not result:
             return "Error: Evaluation returned no result."

        # Format the result as a string for the agent
        output_lines = [
            f"Decision: {result.decision}",
            f"Reason: {result.summary_reason}",
            f"Improvement Prompt: {result.improvement_prompt}",
            "Defects:"
        ]
        
        if result.defects:
            for defect in result.defects:
                output_lines.append(f"  - [Tier {defect.tier}] [{defect.timestamp}] {defect.category}: {defect.description}")
        else:
            output_lines.append("  - None")
            
        output_lines.extend([
            "Scores:",
            f"  - Subject & Brand: {result.category_scores.subject_and_brand}",
            f"  - Physics & Logic: {result.category_scores.physics_and_logic}",
            f"  - Visual Fidelity: {result.category_scores.visual_fidelity}",
            f"  - Temporal Flow: {result.category_scores.temporal_flow}",
            f"  - Consistency: {result.category_scores.consistency}",
            f"  - LLM Score: {result.llm_evaluation_score}",
            f"  - Calculated Score: {result.calculated_evaluation_score}",
            f"  - Averaged Score: {result.averaged_evaluation_score}"
        ])
        
        return "\n".join(output_lines)

    except Exception as e:
        log_message(f"Error in evaluate_ad: {e}", Severity.ERROR)
        return f"Error occurred during evaluation: {str(e)}"