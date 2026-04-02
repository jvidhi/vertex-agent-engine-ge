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

import mimetypes
import sys
import time
from datetime import datetime
import asyncio
from datetime import datetime
from enum import Enum
from typing import List, Any, Dict, Optional

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from tenacity import AsyncRetrying, wait_exponential, stop_after_attempt, retry_if_exception
from google.adk.tools.tool_context import ToolContext
from ..utils import utils_agents
from ..dtos.errors import ShowableException, handle_veo_exception
from ..dtos.generated_media import GeneratedMedia
from ..utils import utils_gcs
from ..utils.constants import get_required_env_var

GOOGLE_CLOUD_PROJECT =  get_required_env_var("GOOGLE_CLOUD_PROJECT")
MODELS_CLOUD_LOCATION = get_required_env_var("MODELS_CLOUD_LOCATION")
GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")

VIDEO_GENERATION_MODEL = get_required_env_var("VIDEO_GENERATION_MODEL")
LOGGING_PREFIX = "[video_generation.py]"
TI2V_PREFIX = "vid_ti2v_"
T2V_PREFIX = "vid_t2v_"

# Map for person generation options
PERSON_GENERATION_MAP = {
    "Allow (All ages)": "allow_all",
    "Allow (Adults only)": "allow_adult",
    "Don't Allow": "dont_allow",
}


def _is_transient_error(exception: BaseException) -> bool:
    """Determine if an exception is transient and should be retried."""
    if isinstance(exception, ClientError):
        # 429: Resource Exhausted, 500+: Server errors
        if exception.code in [429, 500, 502, 503, 504] or "RESOURCE_EXHAUSTED" in str(exception).upper():
            print(f"{LOGGING_PREFIX} Transient error detected (Code {exception.code}). Retrying...")
            return True
        return False
    # Catch any underlying timeout or connection errors
    err_str = str(exception).lower()
    if "timeout" in err_str or "connection" in err_str:
        print(f"{LOGGING_PREFIX} Transient connection error detected. Retrying...")
        return True
    return False

class VideoModality(Enum):
    FIRST_FRAME = "first_frame"
    REFERENCE_IMAGES = "reference_images"


async def generate_video_bytes(
    client: genai.Client,
    model: str,
    prompt: str,
    number_of_videos: int = 1,
    duration_seconds: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    person_generation: Optional[str] = None,
    enhance_prompt: Optional[bool] = None,
    generate_audio: Optional[bool] = None,
    modality: VideoModality = VideoModality.FIRST_FRAME,
    reference_images: Optional[List[types.VideoGenerationReferenceImage]] = None,
    initial_frame_image: Optional[types.Image] = None,
    fps: Optional[int] = None,
    max_retries: int = 4,
    retry_delay_min: float = 2.0,
    retry_delay_max: float = 10.0
) -> List[tuple[bytes, str]]:
    """
    Core function for executing resilient Video API generation.
    Handles async polling, unhandled safety fault translations to the agent, and 429 retries.
    Returns: List of tuples (video_bytes, mime_type)
    """

    operation = None
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_transient_error),
            wait=wait_exponential(multiplier=1, min=retry_delay_min, max=retry_delay_max),
            stop=stop_after_attempt(max_retries),
            reraise=True
        ):
            with attempt:
                if modality == VideoModality.REFERENCE_IMAGES:
                    config = types.GenerateVideosConfig(
                        number_of_videos=number_of_videos,
                        duration_seconds=duration_seconds,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        person_generation=person_generation,
                        enhance_prompt=enhance_prompt,
                        generate_audio=generate_audio,
                        reference_images=reference_images,
                        fps=fps
                    )
                    operation = await client.aio.models.generate_videos(
                        model=model,
                        prompt=prompt,
                        config=config
                    )
                else:  # FIRST_FRAME
                    config = types.GenerateVideosConfig(
                        number_of_videos=number_of_videos,
                        duration_seconds=duration_seconds,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        person_generation=person_generation,
                        enhance_prompt=enhance_prompt,
                        generate_audio=generate_audio,
                        fps=fps
                    )
                    source = types.GenerateVideosSource(
                        prompt=prompt,
                        image=initial_frame_image
                    )
                    operation = await client.aio.models.generate_videos(
                        model=model,
                        source=source,
                        config=config
                    )
    except ClientError as e:
        if _is_transient_error(e):
            raise ShowableException("Video Generation failed: We are heavily throttled or experiencing connectivity issues. You may retry or advise the user to wait.", e)
        else:
            safe_message = f"Video Generation failed due to an API policy or parameter rejection. You MUST rewrite your prompt or modify constraints. Raw error: {str(e)}"
            raise ShowableException(safe_message, e)
    except Exception as e:
        rare_message = f"An unexpected failure occurred during Video generation. You may attempt once more or fail gracefully. Error: {str(e)}"
        raise ShowableException(rare_message, e)

    if not operation:
        raise ShowableException("Failed to initiate video generation.", Exception("Unknown error"))
        

    while not operation.done:
        print(f"{LOGGING_PREFIX} Waiting for video generation async polling to complete...")
        await asyncio.sleep(5)
        operation = await client.aio.operations.get(operation=operation)

    if operation.error:
        # Crucial LLM UX conversion for Veo Safety: 
        # Vertex does not throw Python exceptions for asynchronous backend Veo faults, it just quietly populates operation.error.
        new_exception: Exception = handle_veo_exception(exception_message=str(operation.error))
        
        # We explicitly wrap it so the agent knows HOW to react (by rewriting the prompt).
        showable_err = ShowableException(
            f"Video generation asynchronously failed during Vertex processing. The strict Veo Content Filters caught a violation: {str(new_exception)} \n\n"
            f"You MUST rewrite the video prompt entirely to bypass this filter. Remove sensitive, controversial, and biologically horrific descriptions.",
            new_exception
        )
        raise showable_err

    if not operation.result or not operation.result.generated_videos:
        raise ShowableException("Video operation completed successfully but returned an empty video payload array.", Exception("Empty Video Array"))

    extracted_videos = []
    for generated_video in operation.result.generated_videos:
        if generated_video.video and generated_video.video.video_bytes:
            mime_type = generated_video.video.mime_type or "video/mp4"
            extracted_videos.append((generated_video.video.video_bytes, mime_type))

    if not extracted_videos:
        raise ShowableException("Video metadata was returned but no raw media bytes were attached.", Exception("No Media Bytes"))

    return extracted_videos


