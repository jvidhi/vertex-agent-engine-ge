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

"""image_create_agent: for creating images"""

import mimetypes
import sys
from datetime import datetime
from typing import List, Any, Optional

from google import genai
from google.genai import Client, types
from google.genai.errors import ClientError
from tenacity import AsyncRetrying, wait_exponential, stop_after_attempt, retry_if_exception
from ..utils import utils_agents
from google.adk.tools.tool_context import ToolContext
from ..dtos.errors import ShowableException
from ..dtos.generated_media import GeneratedMedia
from ..utils import utils_gcs
from ..utils.constants import get_required_env_var, get_optional_env_var

GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")
GOOGLE_CLOUD_PROJECT =  get_required_env_var("GOOGLE_CLOUD_PROJECT")
MODELS_CLOUD_LOCATION = get_required_env_var("MODELS_CLOUD_LOCATION")
IMAGE_GENERATION_MODEL = get_required_env_var("IMAGE_GENERATION_MODEL")
IMAGE_DEFAULT_ASPECT_RATIO = get_optional_env_var("IMAGE_DEFAULT_ASPECT_RATIO", "9:16")

LOGGING_PREFIX = "[image_generation.py]"
TI2I_PREFIX = "img_ti2i_"
T2I_PREFIX = "img_t2i_"

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

# Removed generator decorator

async def generate_image_bytes(
    client: Client,
    model: str,
    contents: List[Any],
    max_retries: int = 4,
    retry_delay_min: float = 2.0,
    retry_delay_max: float = 10.0,
    aspect_ratio: Optional[str] = None
) -> List[tuple[bytes, str, str]]:
    """
    Core function for cleanly executing an image generation API call. 
    It leverages tenacity for resilient execution and only catches non-retriable exceptions
    to convert them into ShowableExceptions for agent communication.
    
    Returns: A list of tuples containing (image_bytes, mime_type, model_description_text)
    """

    response = None
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_transient_error),
            wait=wait_exponential(multiplier=1, min=retry_delay_min, max=retry_delay_max),
            stop=stop_after_attempt(max_retries),
            reraise=True
        ):
            with attempt:
                final_aspect_ratio = aspect_ratio or IMAGE_DEFAULT_ASPECT_RATIO

                config = types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                    image_config=types.ImageConfig(aspect_ratio=final_aspect_ratio)
                )
                
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )
    except ClientError as e:
        if _is_transient_error(e):
            # This block is hit if Tenacity exhausts all retries
            raise ShowableException("Image Generation failed: We are heavily throttled or experiencing connectivity issues. You may retry or advise the user to wait.", e)
        else:
            # 400 Invalid Arguments / Safety / Format errors. Elevate this to LLM to take action.
            safe_message = f"Image Generation failed due to an API policy or parameter rejection. You MUST rewrite your prompt or modify constraints. Raw error: {str(e)}"
            raise ShowableException(safe_message, e)
    except Exception as e:
        rare_message = f"An unexpected failure occurred during Image generation. You may attempt once more or fail gracefully. Error: {str(e)}"
        raise ShowableException(rare_message, e)
        
    extracted_images = []
    
    if (
        response
        and response.candidates
        and response.candidates[0]
        and response.candidates[0].content
        and response.candidates[0].content.parts
    ):
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                mime_type = "image/png"
                if hasattr(part.inline_data, "mime_type") and part.inline_data.mime_type:
                    mime_type = part.inline_data.mime_type
                    
                extracted_images.append((part.inline_data.data, mime_type, response.text or ""))

    if not extracted_images:
        raise ShowableException(f"Image Generation succeeded but no actual image payload was returned. The model hallucinated text instead.", Exception("No Images Returned"))
        
    return extracted_images

async def text_and_image_to_image(
    img_prompt: str, image_uri: str, tool_context: ToolContext, image_index: int = 1, aspect_ratio: Optional[str] = None
) -> List[GeneratedMedia]:
    """Generates image(s) from a prompt and saves them.

    This function uses an image-generation model to create images based on the
    provided img_prompt.

    Args:
        img_prompt: The text prompt to use for image generation.
        image_uri: the fully-formed URI for the image to use as input

    Returns:
        On success, it returns a list of GeneratedImage objects.
    """
    
    try:
        
        if image_index < 1:
            image_index = 1

        if image_uri:
            image_uri = utils_gcs.normalize_to_gs_bucket_uri(image_uri)

        print(
            f"{LOGGING_PREFIX} Attempting to generate TI2I image: GCS image_uri: {image_uri}. img_prompt: {img_prompt}."
        )

        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location=MODELS_CLOUD_LOCATION,
        )

        text1 = types.Part.from_text(text=img_prompt)
        image1 = types.Part.from_uri(
            file_uri=image_uri,
            mime_type="image/png",
        )

        model = IMAGE_GENERATION_MODEL
        contents = [text1, image1]

        extracted_images = await generate_image_bytes(
            client=client, model=model, contents=contents, aspect_ratio=aspect_ratio
        )

        generated_images = []
        img_number = 1

        for (image_bytes, mime_type, description) in extracted_images:
            extension = mimetypes.guess_extension(mime_type)
            filename = f"{TI2I_PREFIX}{img_number}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{extension}"
            img_number += 1

            print(
                f"{LOGGING_PREFIX} Generated TI2I: filename: {filename}. description: {description}. mime_type: {mime_type}"
            )
            
            uploaded_file_uri = utils_gcs.upload_to_gcs(
                bucket_path=GOOGLE_CLOUD_BUCKET_ARTIFACTS,
                file_bytes=image_bytes,
                destination_blob_name=filename,
            )
            
            generated_image = GeneratedMedia(
                filename=filename,
                media_bytes=image_bytes,
                description=description,
                mime_type=mime_type,
                title=f"Image {img_number+image_index-2}",
                gcs_uri= utils_gcs.normalize_to_authenticated_url(uploaded_file_uri)
            )

            await utils_agents.save_to_artifact_and_render_asset(
                asset=generated_image,
                context=tool_context,
            )

            generated_images.append(generated_image)

        return generated_images

    except Exception as e:
        print(f"{LOGGING_PREFIX} ERROR in text_and_image_to_image: {e}")
        raise


async def generate_imagen_bytes(
    client: Client,
    model: str,
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: Optional[str] = None,
    max_retries: int = 4,
    retry_delay_min: float = 2.0,
    retry_delay_max: float = 10.0
) -> List[tuple[bytes, str, str]]:
    """
    Core function for cleanly executing an Imagen API call. 
    It leverages tenacity for resilient execution and only catches non-retriable exceptions
    to convert them into ShowableExceptions for agent communication.
    
    Returns: A list of tuples containing (image_bytes, mime_type, model_description_text)
    """
    response = None
    if not aspect_ratio:
        aspect_ratio = IMAGE_DEFAULT_ASPECT_RATIO
        
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_transient_error),
            wait=wait_exponential(multiplier=1, min=retry_delay_min, max=retry_delay_max),
            stop=stop_after_attempt(max_retries),
            reraise=True
        ):
            with attempt:
                response = await client.aio.models.generate_images(
                    model=model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=number_of_images,
                        aspect_ratio=aspect_ratio,
                        output_mime_type="image/png",
                    )
                )
    except ClientError as e:
        if _is_transient_error(e):
            raise ShowableException("Image Generation failed: We are heavily throttled or experiencing connectivity issues. You may retry or advise the user to wait.", e)
        else:
            safe_message = f"Image Generation failed due to an API policy or parameter rejection. You MUST rewrite your prompt or modify constraints. Raw error: {str(e)}"
            raise ShowableException(safe_message, e)
    except Exception as e:
        rare_message = f"An unexpected failure occurred during Image generation. You may attempt once more or fail gracefully. Error: {str(e)}"
        raise ShowableException(rare_message, e)

    if not response:
        raise ShowableException("Failed to generate imagen image.", Exception("Unknown error"))

    if not response.generated_images:
        raise ShowableException(f"Imagen Generation succeeded but no actual image payload was returned.", Exception("No Images Returned"))

    extracted = []
    for i, generated_image in enumerate(response.generated_images):
        if generated_image.image and generated_image.image.image_bytes:
            mime_type = generated_image.image.mime_type or "image/png"
            extracted.append((generated_image.image.image_bytes, mime_type, f"Image {i+1} generated from prompt: {prompt}"))

    return extracted

async def text_to_image(
    img_prompt: str, number_of_images: int, aspect_ratio: str, tool_context: ToolContext, image_index: int = 1
) -> List[GeneratedMedia]:
    """Generates a specified number of images from a prompt and saves them."""

    try:
        client = Client(
            vertexai=True, project=GOOGLE_CLOUD_PROJECT, location=MODELS_CLOUD_LOCATION
        )

        extracted_images = await generate_imagen_bytes(
            client=client,
            model=IMAGE_GENERATION_MODEL,
            prompt=img_prompt,
            number_of_images=number_of_images,
            aspect_ratio=aspect_ratio
        )

        generated_images = []
        for (image_bytes, mime_type, description) in extracted_images:
            extension = mimetypes.guess_extension(mime_type)
            filename = f"{T2I_PREFIX}{image_index}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{extension}"
            image_index += 1
            print(
                f"{LOGGING_PREFIX} Generated T2I: filename: {filename}. mime_type: {mime_type}"
            )

            uploaded_file_uri = utils_gcs.upload_to_gcs(
                bucket_path=GOOGLE_CLOUD_BUCKET_ARTIFACTS,
                file_bytes=image_bytes,
                destination_blob_name=filename,
            )
            
            generated_image = GeneratedMedia(
                filename=filename,
                media_bytes=image_bytes,
                mime_type=mime_type,
                description=description,
                title=f"Image {image_index-1}",
                gcs_uri= utils_gcs.normalize_to_authenticated_url(uploaded_file_uri)
            )
        
            await utils_agents.save_to_artifact_and_render_asset(
                asset=generated_image,
                context=tool_context,
            )
        
            generated_images.append(generated_image)

        return generated_images
    except ClientError as e:
        if _is_transient_error(e):
            # This block is only hit if Tenacity exhausts all retries
            user_message = "We're experiencing high demand or connectivity issues right now. Please try again in a few moments."
            print(f"{LOGGING_PREFIX} ERROR generating image: User-friendly error: {user_message}")
            raise ShowableException(user_message, e)
        else:
            print(f"{LOGGING_PREFIX} ERROR: Unexpected Generative AI error: {e}")
            raise
