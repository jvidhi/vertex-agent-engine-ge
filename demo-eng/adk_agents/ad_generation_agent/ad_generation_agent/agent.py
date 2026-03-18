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
"""Initializes and configures the main content generation agent.

This script sets up the root agent responsible for orchestrating the entire
ad generation workflow. It defines the agent's instructions, registers all
necessary tools, and configures the underlying language model.
"""

import json
import os
from typing import Any

import requests
import vertexai
from adk_common.utils.constants import (get_optional_env_var,
                                        get_required_env_var)
from adk_common.utils.env_loader import load_env_cascade

# Load environment variables (Standalone or Linked Mode)
load_env_cascade(__file__)
from adk_common.utils.utils_logging import Severity, log_message
from google.adk.agents.readonly_context import ReadonlyContext


# --- 1. Environment Variable Retrieval ---
GOOGLE_CLOUD_PROJECT = get_required_env_var("GOOGLE_CLOUD_PROJECT")
LLM_GEMINI_MODEL_ADGEN_ROOT = get_required_env_var("LLM_GEMINI_MODEL_ADGEN_ROOT")
DEMO_COMPANY_NAME = get_optional_env_var("DEMO_COMPANY_NAME", "ACME Corp")

# --- 2. Configure Model Location ---
# Initialize Vertex AI
MODEL_LOCATION = get_required_env_var("GOOGLE_CLOUD_LOCATION")

log_message(f"GOOGLE_CLOUD_PROJECT: {GOOGLE_CLOUD_PROJECT}", Severity.DEBUG)
log_message(f"LLM_GEMINI_MODEL_ADGEN_ROOT: {LLM_GEMINI_MODEL_ADGEN_ROOT}", Severity.DEBUG)
log_message(f"Model location set to: {MODEL_LOCATION}", Severity.DEBUG)
log_message(f"Effective GOOGLE_CLOUD_LOCATION for genai client: {os.environ.get('GOOGLE_CLOUD_LOCATION')}", Severity.DEBUG)
log_message(f"GOOGLE_GENAI_USE_VERTEXAI: {os.environ.get('GOOGLE_GENAI_USE_VERTEXAI')}", Severity.DEBUG)

# This configures the default location for calls made *directly* through the vertexai SDK
# and influences the google-genai library when used with Vertex AI.
try:
    vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=MODEL_LOCATION)
    log_message(f"vertexai.init() called successfully with project={GOOGLE_CLOUD_PROJECT}, location={MODEL_LOCATION}", Severity.DEBUG)
except Exception as e:
    log_message(f"Error initializing Vertex AI SDK: {e}. Continuing, as google-genai env vars might suffice.", Severity.DEBUG)

from adk_common.utils import utils_gcs, utils_prompts
from adk_common.utils.utils_agents import SESSION_ARTIFACTS_STATE_KEY
from adk_common.dtos.generated_media import GeneratedMedia
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types
from .func_tools.combine_video import combine
from .func_tools.generate_audio import generate_audio_and_voiceover
from .func_tools.generate_asset_sheet import generate_asset_sheet
from .func_tools.generate_scene_frame import generate_scene_frame
from .func_tools.generate_storyboard_image_batch import generate_storyboard_image_batch
from .func_tools.generate_ad_hoc_image import generate_ad_hoc_image, generate_ad_hoc_image_batch
from .func_tools.generate_display_ad import generate_display_ad
from .func_tools.generate_video import (
    generate_video_from_first_frame,
    generate_video_from_reference_images,
    generate_video_storyboard_batch
)
from .func_tools.retrieve_generated_assets import retrieve_generated_assets
from .func_tools.evaluate_ad import evaluate_ad
from .func_tools.retrieve_brand_identity import retrieve_brand_identity
from .utils.storytelling import STORYTELLING_INSTRUCTIONS


from ad_generation_agent.utils import ad_generation_constants

async def _dynamic_instruction_provider(
    context: ReadonlyContext,
) -> str:
    """Dynamically provides instructions to the agent by loading and formatting a prompt."""
    prompt = utils_prompts.load_prompt_file_from_calling_agent(
        {
            "DEMO_COMPANY_NAME": DEMO_COMPANY_NAME,
            "STORYTELLING_INSTRUCTIONS": STORYTELLING_INSTRUCTIONS,
            "GCS_AUTHENTICATED_DOMAIN": utils_gcs.GCS_AUTHENTICATED_DOMAIN,
            "GCS_AUTHENTICATED_DOMAIN_SANS_PROTOCOL": utils_gcs.GCS_AUTHENTICATED_DOMAIN_SANS_PROTOCOL,
            "SESSION_ARTIFACTS_STATE": json.dumps(context.state.get(SESSION_ARTIFACTS_STATE_KEY, "{}")),
            "PRODUCT_IMAGE_URL": context.state.get(ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL, ""),
            "PRODUCT_NAME": context.state.get(ad_generation_constants.STATE_KEY_PRODUCT_NAME, ""),
            "LOGO_IMAGE_URL": context.state.get(ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL, ""),
            "MAIN_CHARACTER_URL": context.state.get(ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL, ""),
            "ASSET_SHEET_URL": context.state.get(ad_generation_constants.STATE_KEY_ASSET_SHEET_URL, ""),
            "BRAND_CONTEXT_PAYLOAD": json.dumps(context.state.get(ad_generation_constants.STATE_KEY_BRAND_CONTEXT_PAYLOAD, {})),
            "GENERATE_ASSET_SHEET_TOOL": generate_asset_sheet.__name__,
            "COMBINE_TOOL": combine.__name__,
            "GENERATE_AUDIO_AND_VOICEOVER_TOOL": generate_audio_and_voiceover.__name__,
            "GENERATE_SCENE_FRAME_TOOL": generate_scene_frame.__name__,
            "GENERATE_STORYBOARD_IMAGE_BATCH_TOOL": generate_storyboard_image_batch.__name__,
            "GENERATE_AD_HOC_IMAGE_TOOL": generate_ad_hoc_image.__name__,
            "GENERATE_AD_HOC_IMAGE_BATCH_TOOL": generate_ad_hoc_image_batch.__name__,
            "GENERATE_DISPLAY_AD_TOOL": generate_display_ad.__name__,
            "GENERATE_VIDEO_FROM_FIRST_FRAME_TOOL": generate_video_from_first_frame.__name__,
            "GENERATE_VIDEO_FROM_REFERENCE_IMAGES_TOOL": generate_video_from_reference_images.__name__,
            "GENERATE_VIDEO_STORYBOARD_BATCH_TOOL": generate_video_storyboard_batch.__name__,
            "RETRIEVE_GENERATED_ASSETS_TOOL": retrieve_generated_assets.__name__,
            "EVALUATE_AD_TOOL": evaluate_ad.__name__,
            "RETRIEVE_BRAND_IDENTITY_TOOL": retrieve_brand_identity.__name__,
            "CONFIRM_URL_EXISTS_TOOL": confirm_url_exists.__name__,
        }
    )
    return prompt


def _before_tool_callback(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext) -> dict | None:

    log_message(f"Tool Call: {tool.name}", Severity.INFO)
    log_message(f"Arguments: {args}", Severity.INFO)


def confirm_url_exists(url: str) -> bool:
    """Confirm if a URL exists."""
    try:
        response = requests.get(url, timeout=10)
        return response.status_code == 200
    except Exception as e:
        log_message(f"Error checking URL {url}: {e}", Severity.ERROR)
        return False


root_agent = LlmAgent(
    name="content_generation_agent",
    model=LLM_GEMINI_MODEL_ADGEN_ROOT,
    instruction=_dynamic_instruction_provider,
    tools=[
        FunctionTool(func=generate_asset_sheet),
        FunctionTool(func=generate_scene_frame),
        FunctionTool(func=generate_storyboard_image_batch),
        FunctionTool(func=generate_ad_hoc_image),
        FunctionTool(func=generate_ad_hoc_image_batch),
        FunctionTool(func=generate_display_ad),
        FunctionTool(func=generate_video_from_first_frame),
        FunctionTool(func=generate_video_from_reference_images),
        FunctionTool(func=generate_video_storyboard_batch),
        FunctionTool(func=generate_audio_and_voiceover),
        FunctionTool(func=combine),
        FunctionTool(func=retrieve_generated_assets),
        FunctionTool(func=evaluate_ad),
        FunctionTool(func=retrieve_brand_identity),
        FunctionTool(func=confirm_url_exists),
    ],
    before_tool_callback=_before_tool_callback,
)
