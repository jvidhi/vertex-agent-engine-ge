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

"""genmedia_agent: A resilient agent for creating, editing, storyboarding images, and generating video, with built-in fallbacks."""

import json
import sys
import traceback
from typing import Any, List, Optional

import requests
from adk_common.dtos.agent_tool_response import (
    AgentToolResponse, AgentToolResponseGenMedia, Status)
from adk_common.dtos.errors import ShowableException
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.media_generation.image_generation import (text_and_image_to_image,
                                                       text_to_image)
from adk_common.media_generation.video_generation import generate_video_bytes, VideoModality
from adk_common.utils import utils_agents, utils_gcs, utils_prompts
from adk_common.utils.constants import (get_optional_env_var,
                                               get_required_env_var)
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool
from google.adk.tools.agent_tool import AgentTool
from google.adk.utils import instructions_utils
from pydantic import ValidationError

# Environment Variables
GOOGLE_CLOUD_PROJECT = get_required_env_var("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")
AGENT_VERSION = get_required_env_var("AGENT_VERSION")
IS_DEBUG_ON = get_optional_env_var("IS_DEBUG_ON", "False").lower() in ("true", "1", "t")
DEMO_COMPANY_NAME = get_optional_env_var("DEMO_COMPANY_NAME", "our company")

# Main Agent LLM
LLM_GEMINI_MODEL_GENMEDIA = get_required_env_var("LLM_GEMINI_MODEL_GENMEDIA")

# Image Generation Models & Settings
IMAGE_GENERATION_MODEL = get_required_env_var("IMAGE_GENERATION_MODEL")
IMAGE_EDITION_MODEL = get_required_env_var("IMAGE_EDITION_MODEL")
MAX_NUMBER_OF_IMAGES = get_required_env_var("MAX_NUMBER_OF_IMAGES")
IMAGE_DEFAULT_ASPECT_RATIO = get_required_env_var("IMAGE_DEFAULT_ASPECT_RATIO")
NUMBER_OF_STORYBOARD_SCENES = get_required_env_var("NUMBER_OF_STORYBOARD_SCENES")

# Video Generation Models & Settings
VIDEO_GENERATION_MODEL = get_required_env_var("VIDEO_GENERATION_MODEL")
MAX_NUMBER_OF_VIDEOS = get_optional_env_var("MAX_NUMBER_OF_VIDEOS", "1")  # Default to 1
VIDEO_DEFAULT_ASPECT_RATIO = get_required_env_var("VIDEO_DEFAULT_ASPECT_RATIO")
VIDEO_DEFAULT_RESOLUTION = get_required_env_var("VIDEO_DEFAULT_RESOLUTION")
VIDEO_DEFAULT_DURATION = get_optional_env_var(
    "VIDEO_DEFAULT_DURATION", "4"
)  # Default to 4

# Agent Configuration
GENMEDIA_IMAGE_OUTPUT_KEY = "GENMEDIA_IMAGE_OUTPUT_KEY"
GENMEDIA_VIDEO_OUTPUT_KEY = "GENMEDIA_VIDEO_OUTPUT_KEY"
LOGGING_PREFIX = f"[##MARKETING_AGENT_GENMEDIA_{AGENT_VERSION}]"

GENMEDIA_AGENT_DESCRIPTION = """
A creative and detail-oriented AI generative media specialist. Its expertise lies in translating marketing concepts into visually compelling and on-brand images, storyboards, and videos.
Objective: generate, edit, and manage marketing images, storyboards, and videos based on user requests. The agent can generate new images from text, edit existing images, create storyboard sequences, or generate videos from text or images.
"""

# Tool-specific constants
IMAGE_ASPECT_RATIO_OPTIONS = ["1:1", "3:4", "4:3", "9:16", "16:9"]
VIDEO_ASPECT_RATIO_OPTIONS = ["16:9", "9:16"]
VIDEO_RESOLUTION_OPTIONS = ["720p", "1080p"]
VIDEO_DURATION_OPTIONS = ["4", "6", "8"]

IMAGE_MIMETYPES = {"image/png", "image/jpeg"}
VIDEO_MIMETYPES = {"video/mp4"}
ALLOWED_MIMETYPES = IMAGE_MIMETYPES | VIDEO_MIMETYPES

DEBUG_INSTRUCTIONS = (
    " "
    if not IS_DEBUG_ON
    else """## DEBUG MODE
You are currently running in debug mode. Before any function call, when deciding on a new step or when receiving a response (successful or not), call `Debug` tool with a descriptive `message`
"""
)


async def _dynamic_instruction_provider(
    context: ReadonlyContext,
) -> str:
    """Provides the dynamic instructions for the LLM agent."""
    prompt = utils_prompts.load_prompt_file_from_calling_agent(
        {
            # Models
            "IMAGE_GENERATION_MODEL": IMAGE_GENERATION_MODEL,
            "IMAGE_EDITION_MODEL": IMAGE_EDITION_MODEL,
            "VIDEO_GENERATION_MODEL": VIDEO_GENERATION_MODEL,
            
            # Primary Tool Names
            "CONFIRM_REFERENCE_TOOL_NAME": _confirm_valid_url.__name__,
            "GENERATE_IMAGE_FROM_TEXT_TOOL_NAME": _generate_image_from_text.__name__,
            "GENERATE_IMAGE_FROM_IMAGE_TOOL_NAME": _generate_image_from_image.__name__,
            "GENERATE_VIDEO_TOOL_NAME": _generate_video_from_text_or_image.__name__,
            
            # Config
            "GENMEDIA_IMAGE_OUTPUT_KEY": GENMEDIA_IMAGE_OUTPUT_KEY,
            "GENMEDIA_VIDEO_OUTPUT_KEY": GENMEDIA_VIDEO_OUTPUT_KEY,
            "MAX_NUMBER_OF_IMAGES": MAX_NUMBER_OF_IMAGES,
            "IMAGE_DEFAULT_ASPECT_RATIO": IMAGE_DEFAULT_ASPECT_RATIO,
            "NUMBER_OF_STORYBOARD_SCENES": NUMBER_OF_STORYBOARD_SCENES,
            "MAX_NUMBER_OF_VIDEOS": MAX_NUMBER_OF_VIDEOS,
            "VIDEO_DEFAULT_DURATION": VIDEO_DEFAULT_DURATION,
            "VIDEO_DEFAULT_ASPECT_RATIO": VIDEO_DEFAULT_ASPECT_RATIO,
            "VIDEO_DEFAULT_RESOLUTION": VIDEO_DEFAULT_RESOLUTION,
            "IMAGE_ASPECT_RATIO_OPTIONS": json.dumps(IMAGE_ASPECT_RATIO_OPTIONS),
            "VIDEO_ASPECT_RATIO_OPTIONS": json.dumps(VIDEO_ASPECT_RATIO_OPTIONS),
            "VIDEO_DURATION_OPTIONS": json.dumps(VIDEO_DURATION_OPTIONS),
            "GOOGLE_CLOUD_BUCKET_ARTIFACTS": GOOGLE_CLOUD_BUCKET_ARTIFACTS,
            "ALLOWED_MIMETYPES": json.dumps(list(ALLOWED_MIMETYPES)),
            "GCS_AUTHENTICATED_DOMAIN": utils_gcs.GCS_AUTHENTICATED_DOMAIN,
            "DEMO_COMPANY_NAME": DEMO_COMPANY_NAME,
            "AGENT_VERSION": AGENT_VERSION,
            # Debug
            "DEBUG_INSTRUCTIONS": DEBUG_INSTRUCTIONS,
        }
    )
    return await instructions_utils.inject_session_state(prompt, context)


def _before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Logs the request details before calling the model."""
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    current_state = json.dumps(callback_context.state.to_dict())

    print(f"{LOGGING_PREFIX} Before Model Callback")
    print(f"{LOGGING_PREFIX} Starting Agent: {agent_name} (Inv: {invocation_id})")
    print(f"{LOGGING_PREFIX} Current State: {current_state}")
    print(
        f"{LOGGING_PREFIX} Request: {utils_agents.stringify_llm_request(llm_request)}"
    )
    print(f"{LOGGING_PREFIX} User Content: {callback_context.user_content}")
    return None  # Allow the model call to proceed


def _after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """Logs the response details after the model call."""
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    current_state = json.dumps(callback_context.state.to_dict())

    print(f"{LOGGING_PREFIX} After Model Callback")
    print(f"{LOGGING_PREFIX} Current State: {current_state}")
    print(
        f"{LOGGING_PREFIX} LLM Response: {utils_agents.stringify_llm_response(llm_response)}"
    )
    print(f"{LOGGING_PREFIX} Exiting Agent: {agent_name} (Inv: {invocation_id})")
    return None  # Allow the model call to proceed


def _confirm_valid_url(reference: str, mimetype: str):
    exists = False
    if mimetype in ALLOWED_MIMETYPES:
        exists, _ = utils_agents.check_asset_exists(reference=reference, expected_content_types={mimetype})
    
    if exists:
        return AgentToolResponse(status=Status.SUCCESS, detail=f"URL {reference} is valid").convert_to_agent_response()
    else:
        message = "Image is invalid or does not exist"
        print(f"{LOGGING_PREFIX} ERROR: {message}", file=sys.stderr)
        return AgentToolResponse(status=Status.ERROR, detail=message).convert_to_agent_response()


def _update_agent_state(
    generated_media: List[GeneratedMedia], tool_context: ToolContext, state_key: str
):
    """Helper to safely update a specific output state list."""
    new_state = []
    current_state = tool_context.state.get(state_key)
    if current_state and isinstance(current_state, list):
        new_state.extend(current_state)
    new_state.extend([item.to_obj_sans_bytes() for item in generated_media])
    tool_context.state[state_key] = new_state


async def _debug(message: str, tool_context: ToolContext):
    """A tool for printing debug messages. Only use if in debug mode."""
    utils_agents.geminienterprise_print(context=tool_context, message=message)
    print(f"{LOGGING_PREFIX} ##DEBUG: {message}")
    return AgentToolResponse(status=Status.SUCCESS).convert_to_agent_response()


async def _generate_image_from_image(
    img_prompt: str, image_uri: str, aspect_ratio: str, tool_context: ToolContext
):
    """Generates, edits, or creates storyboard scenes from a prompt and an existing image."""
    print(f"{LOGGING_PREFIX} Image-to-Image prompt: `{img_prompt}`.")
    print(f"{LOGGING_PREFIX} Source Image URI: `{image_uri}`.")

    try:
        image_index: int = 0
        current_state = tool_context.state.get(GENMEDIA_IMAGE_OUTPUT_KEY)
        if current_state and isinstance(current_state, list):
            image_index = len(current_state)
        
        if not aspect_ratio or aspect_ratio not in IMAGE_ASPECT_RATIO_OPTIONS:
            aspect_ratio = IMAGE_DEFAULT_ASPECT_RATIO
            
        generated_images = await text_and_image_to_image(
            image_uri=image_uri,
            img_prompt=img_prompt,
            tool_context=tool_context,
            image_index=image_index+1,
            aspect_ratio=aspect_ratio
        )

        _update_agent_state(generated_images, tool_context, GENMEDIA_IMAGE_OUTPUT_KEY)
        print(f"{LOGGING_PREFIX} Image-to-Image Response: {json.dumps([item.to_obj_sans_bytes() for item in generated_images])}")

        return AgentToolResponseGenMedia(status=Status.SUCCESS, 
                                 detail=f"{len(generated_images)} images generated successfully.",
                                 generated_media=generated_images).convert_to_agent_response()
    except ShowableException as e:
        print(
            f"{LOGGING_PREFIX} ERROR. Image-to-Image. ShowableException: {e.showable_message}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail=e.showable_message).convert_to_agent_response()
    except requests.exceptions.HTTPError as e:
        error_response = f"There was an HTTP error ({e.response.status_code})"
        if e.response.status_code == 404:
            error_response = (
                f"The URI provided (`{image_uri}` was not found (HTTP 404 error)"
            )
        print(
            f"{LOGGING_PREFIX} ERROR. Image-to-Image. HTTPError: {error_response}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail=error_response).convert_to_agent_response()
    except Exception as e:
        print(
            f"{LOGGING_PREFIX} ERROR. Image-to-Image. Exception: {e}", file=sys.stderr
        )
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail="There was an unknown error generating images. Follow your instructions to determine next steps.").convert_to_agent_response()


async def _generate_image_from_text(
    img_prompt: str, number_of_images: int, aspect_ratio: str, tool_context: ToolContext
):
    """Generates a specified number of new images from a text prompt."""
    print(f"{LOGGING_PREFIX} Text-to-Image prompt: `{img_prompt}`.")
    print(f"{LOGGING_PREFIX} Number of requested images: `{number_of_images}`")
    print(f"{LOGGING_PREFIX} Requested aspect ratio: `{aspect_ratio}`")

    try:
        number_of_images = utils_agents.get_number_of_images(
            number_of_images, MAX_NUMBER_OF_IMAGES
        )
        if not aspect_ratio or aspect_ratio not in IMAGE_ASPECT_RATIO_OPTIONS:
            aspect_ratio = IMAGE_DEFAULT_ASPECT_RATIO
        print(
            f"{LOGGING_PREFIX} Actual images: `{number_of_images}`. Actual aspect ratio: {aspect_ratio}."
        )

        image_index: int = 0
        current_state = tool_context.state.get(GENMEDIA_IMAGE_OUTPUT_KEY)
        if current_state and isinstance(current_state, list):
            image_index = len(current_state)
            
        generated_images = await text_to_image(
            img_prompt=img_prompt,
            number_of_images=number_of_images,
            aspect_ratio=aspect_ratio,
            tool_context=tool_context,
            image_index=image_index+1
        )

        _update_agent_state(generated_images, tool_context, GENMEDIA_IMAGE_OUTPUT_KEY)
        print(f"{LOGGING_PREFIX} Text-to-Image Response: {json.dumps([item.to_obj_sans_bytes() for item in generated_images])}")
        return AgentToolResponseGenMedia(status=Status.SUCCESS, 
                                 detail=f"{len(generated_images)} images generated successfully.",
                                 generated_media=generated_images).convert_to_agent_response()
    except ShowableException as e:
        print(
            f"{LOGGING_PREFIX} ERROR. Text-to-Image. ShowableException: {e.showable_message}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail=e.showable_message).convert_to_agent_response()
    except Exception as e:
        print(f"{LOGGING_PREFIX} ERROR. Text-to-Image. Exception: {e}", file=sys.stderr)
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail="There was an unknown error generating images. Follow your instructions to determine next steps.").convert_to_agent_response()


async def _generate_video_from_text_or_image(
    vid_prompt: str,
    image_uri: str,
    duration_seconds: int,
    tool_context: ToolContext,
    aspect_ratio: str,
    resolution: str,
):
    """Generates videos from a prompt OR an existing image and saves them."""
    print(f"{LOGGING_PREFIX} Video generation prompt: `{vid_prompt}`.")
    print(f"{LOGGING_PREFIX} Reference image GCS URI: `{image_uri}`.")
    print(f"{LOGGING_PREFIX} Requested duration: `{duration_seconds}` seconds.")
    print(f"{LOGGING_PREFIX} Requested aspect ratio: `{aspect_ratio}`")
    print(f"{LOGGING_PREFIX} Requested resolution: `{resolution}`")

    try:
        number_of_videos = int(MAX_NUMBER_OF_VIDEOS)
        if not aspect_ratio or aspect_ratio not in VIDEO_ASPECT_RATIO_OPTIONS:
            aspect_ratio = VIDEO_DEFAULT_ASPECT_RATIO
        if not resolution or resolution not in VIDEO_RESOLUTION_OPTIONS:
            resolution = VIDEO_DEFAULT_RESOLUTION
        if not duration_seconds or duration_seconds not in VIDEO_DURATION_OPTIONS:
            duration_seconds = int(VIDEO_DEFAULT_DURATION)
        print(
            f"{LOGGING_PREFIX} Actual videos: `{number_of_videos}`. Actual aspect ratio: {aspect_ratio}. Actual resolution: {resolution}."
        )

        video_index: int = 0
        current_state = tool_context.state.get(GENMEDIA_VIDEO_OUTPUT_KEY)
        if current_state and isinstance(current_state, list):
            video_index = len(current_state)
            
        from google import genai
        from google.genai import types
        from datetime import datetime
        import mimetypes
        
        MODELS_CLOUD_LOCATION = get_required_env_var("MODELS_CLOUD_LOCATION")
        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location=MODELS_CLOUD_LOCATION,
        )
        
        initial_frame_image = None
        if image_uri:
            image_uri = utils_gcs.normalize_to_gs_bucket_uri(image_uri)
            print(f"{LOGGING_PREFIX} Reference image GCS URI: `{image_uri}`.")
            initial_frame_image = types.Image(gcs_uri=image_uri, mime_type="image/png")
            
        extracted_videos = await generate_video_bytes(
            client=client,
            model=VIDEO_GENERATION_MODEL,
            prompt=vid_prompt,
            number_of_videos=1,
            duration_seconds=int(duration_seconds),
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            person_generation="allow_all",
            enhance_prompt=True,
            generate_audio=True,
            modality=VideoModality.FIRST_FRAME,
            initial_frame_image=initial_frame_image
        )

        generated_videos: List[GeneratedMedia] = []
        for i, (video_bytes, mime_type) in enumerate(extracted_videos):    
            extension = mimetypes.guess_extension(mime_type)

            prefix = "TI2V_" if image_uri else "T2V_"
            filename = f"{prefix}{i+video_index}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{extension}"
            
            print(f"{LOGGING_PREFIX} Generated video: filename: {filename}. mime_type: {mime_type}")
            
            uploaded_file_uri = utils_gcs.upload_to_gcs(
                bucket_path=GOOGLE_CLOUD_BUCKET_ARTIFACTS,
                file_bytes=video_bytes,
                destination_blob_name=filename,
            )
            
            generated_video = GeneratedMedia(
                filename=filename,
                media_bytes=video_bytes,
                mime_type=mime_type,
                description=f"Video {i+video_index} generated from prompt: {vid_prompt}",
                title=f"Video {i+video_index}",
                gcs_uri= utils_gcs.normalize_to_authenticated_url(uploaded_file_uri)
            )
            
            await utils_agents.save_to_artifact_and_render_asset(
                asset=generated_video,
                context=tool_context,
            )
            
            generated_videos.append(generated_video)
        
        _update_agent_state(generated_videos, tool_context, GENMEDIA_VIDEO_OUTPUT_KEY)
        json_response = json.dumps([item.to_obj_sans_bytes() for item in generated_videos])
        print(f"{LOGGING_PREFIX} Video Generation Response: {json_response}")

        return AgentToolResponseGenMedia(status=Status.SUCCESS, 
                                 detail=f"{len(generated_videos)} videos generated successfully.",
                                 generated_media=generated_videos).convert_to_agent_response()
    except ShowableException as e:
        print(
            f"{LOGGING_PREFIX} ERROR. Video Generation. ShowableException: {e.showable_message}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail=e.showable_message).convert_to_agent_response()
    except Exception as e:
        print(
            f"{LOGGING_PREFIX} ERROR. Video Generation. Exception: {e}", file=sys.stderr
        )
        traceback.print_exc()
        return AgentToolResponse(status=Status.ERROR, detail="There was an unknown error generating the video. Follow your instructions to determine next steps.").convert_to_agent_response()


# --- Define the unified LlmAgent ---
agent_tools = [
    _generate_image_from_text,
    _generate_image_from_image,
    _generate_video_from_text_or_image,
    _confirm_valid_url,
    LoadArtifactsTool,
]

if IS_DEBUG_ON:
    agent_tools.append(_debug)

genmedia_agent = LlmAgent(
    model=LLM_GEMINI_MODEL_GENMEDIA,
    name="genmedia_agent",
    description=GENMEDIA_AGENT_DESCRIPTION,
    instruction=_dynamic_instruction_provider,
    tools=agent_tools,
    before_model_callback=_before_model_callback,
    after_model_callback=_after_model_callback,
)

root_agent = genmedia_agent
