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


# Removed decorator

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
    reference_images: Optional[List[Any]] = None,
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


async def text_or_image_to_video(
    vid_prompt: str, image_uri: str, duration_seconds: int, aspect_ratio: str, resolution: str, tool_context: ToolContext, video_index: int = 1,
) -> List[GeneratedMedia]:
    """
    Generates a video from a text prompt and saves it.
    This function uses an video generation model to create videos based on the
    provided text prompt. Each generated video is saved as a MP4 file artifact.

    Args:
        prompt: The text prompt to use for video generation.
        image_uri: the fully-formed URI for the image to use as input for the video.
        duration_seconds: The duration of the generated video in seconds (can be one of [8,6,4] for image_to_video).
        aspect_ratio: The aspect ratio of the generated video (can be one of ["16:9", "9:16"]).
        resolution: The resolution of the generated video (can be one of ["720p", "1080p"]).
        video_index: The index of the first video to generate (for naming).

    Returns:
        On success, it returns a list of generated GCS uri. Unlike image generation, 
        the video is generated and stored in GCS.
    """

    client = genai.Client(
        vertexai=True,
        project=GOOGLE_CLOUD_PROJECT,
        location=MODELS_CLOUD_LOCATION,
    )

    operation: types.GenerateVideosOperation
    
    try:
        reference_images = None
        if image_uri:
            image_uri = utils_gcs.normalize_to_gs_bucket_uri(image_uri)
            print(f"{LOGGING_PREFIX} Reference image GCS URI: `{image_uri}`.")
            reference_images = [
                types.VideoGenerationReferenceImage(
                    image=types.Image(gcs_uri=image_uri, mime_type="image/png"),
                    reference_type=types.VideoGenerationReferenceType("asset")
                )
            ]

        extracted_videos = await generate_video_bytes(
            client=client,
            model=VIDEO_GENERATION_MODEL,
            prompt=vid_prompt,
            number_of_videos=1,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            person_generation=PERSON_GENERATION_MAP["Allow (All ages)"],
            enhance_prompt=True,
            generate_audio=True,
            reference_images=reference_images
        )

        generated_videos: List[GeneratedMedia] = []
        for i, (video_bytes, mime_type) in enumerate(extracted_videos):    
            extension = mimetypes.guess_extension(mime_type)

            if image_uri:
                filename = f"{TI2V_PREFIX}{i+video_index}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{extension}"
            else:
                filename = f"{T2V_PREFIX}{i+video_index}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{extension}"
            
            print(f"{LOGGING_PREFIX} Generated T2V: filename: {filename}. mime_type: {mime_type}")
            
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

        return generated_videos

    except Exception as e:
        print(f"{LOGGING_PREFIX} ERROR in text_or_image_to_video: {e}")
        raise
    
    return generated_videos
