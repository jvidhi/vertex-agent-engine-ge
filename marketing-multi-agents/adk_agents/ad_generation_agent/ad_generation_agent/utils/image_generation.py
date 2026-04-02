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
"""Utility functions for interacting with the Gemini API."""
import datetime
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple, cast
import asyncio
import mimetypes

from ad_generation_agent.utils.evaluate_media import evaluate_media
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import Severity, log_message, log_function_call
from aiohttp import ClientError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential
from .eval_result import EvalResult
from google import auth, genai
from adk_common.dtos.errors import ShowableException
from adk_common.media_generation.image_generation import generate_image_bytes
from google.api_core import exceptions as api_exceptions
from google.auth import exceptions as auth_exceptions
from google.genai import types
from google.genai.types import HarmBlockThreshold, HarmCategory

IMAGE_MIME_TYPE = "image/png"


def get_image_generation_tenacity_attempts() -> int:
    return int(get_required_env_var("IMAGE_GENERATION_TENACITY_ATTEMPTS"))


def get_image_generation_eval_reattempts() -> int:
    return int(get_required_env_var("IMAGE_GENERATION_EVAL_REATTEMPTS"))


def get_image_generation_concurrency_limit() -> int:
    return int(get_required_env_var("IMAGE_GENERATION_CONCURRENCY_LIMIT"))


def get_image_generation_retry_delay_seconds() -> int:
    return int(get_required_env_var("IMAGE_GENERATION_RETRY_DELAY_SECONDS"))


def get_image_generation_model() -> str:
    return get_required_env_var("IMAGE_GENERATION_MODEL")


def get_google_cloud_project() -> str:
    return get_required_env_var("GOOGLE_CLOUD_PROJECT")


def get_models_cloud_location() -> str:
    return get_required_env_var("MODELS_CLOUD_LOCATION")


def get_google_genai_use_vertexai() -> bool:
    return bool(get_required_env_var("GOOGLE_GENAI_USE_VERTEXAI"))

def get_image_default_aspect_ratio() -> str:
    return get_required_env_var("IMAGE_DEFAULT_ASPECT_RATIO")


def get_gemini_client() -> genai.Client:
    """Initializes and returns a Gemini client.

    Returns:
        A genai.Client instance or None if initialization fails.
    """
    # We do not cache the client globally because it binds to the current event loop.
    # In environments like Reasoning Engine where loops might be recreated or distinct per request,
    # reusing a client created in a different loop causes "Future attached to a different loop" errors.
    try:
        return genai.Client(
            vertexai=get_google_genai_use_vertexai(),
            project=get_google_cloud_project(),
            location=get_models_cloud_location(),
        )
    except Exception as e:
        log_message(f"ERROR: Failed to initialize Gemini client: {e}", Severity.ERROR)
        import traceback
        log_message(f"Traceback: {traceback.format_exc()}", Severity.DEBUG)
        raise e


SAFETY_SETTINGS = [
    types.SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=HarmBlockThreshold.OFF,
    ),
    types.SafetySetting(
        category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=HarmBlockThreshold.OFF,
    ),
    types.SafetySetting(
        category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=HarmBlockThreshold.OFF,
    ),
    types.SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=HarmBlockThreshold.OFF,
    ),
]

def _log_env_vars():
    log_message(f"DEBUG_ENV: GOOGLE_CLOUD_PROJECT={os.environ.get('GOOGLE_CLOUD_PROJECT')}", Severity.DEBUG)
    log_message(f"DEBUG_ENV: MODELS_CLOUD_LOCATION={os.environ.get('MODELS_CLOUD_LOCATION')}", Severity.DEBUG)
    log_message(f"DEBUG_ENV: IMAGE_GENERATION_MODEL={os.environ.get('IMAGE_GENERATION_MODEL')}", Severity.DEBUG)
    log_message(f"DEBUG_ENV: GOOGLE_GENAI_USE_VERTEXAI={os.environ.get('GOOGLE_GENAI_USE_VERTEXAI')}", Severity.DEBUG)


# @log_function_call
async def generate_and_select_best_image(
    prompt: str,
    input_images: List[types.Part],
    filename_without_extension: str,
    allow_collage: bool = False,
    tool_context: Any = None,
    log_prefix: str = "Image",
    input_image_descriptions: Optional[List[str]] = None,
    aspect_ratio: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates a single image using Gemini, handling retries for errors or low evaluation scores.

    Args:
        prompt (str): The prompt for image generation.
        input_images (List[types.Part]): A list of input images as Part objects.
        filename_without_extension (str): The filename for the output image, without extension.
        allow_collage (bool): If True, allows collages and storyboards. Defaults to False.
        tool_context (Any, optional): The agent context for streaming UI logs.
        log_prefix (str, optional): The prefix used when logging to UI (e.g. "Scene 1").
        input_image_descriptions (List[str], optional): Parallel descriptions for the input images, passed directly to the evaluator.

    Returns:
        Dict[str, Any]: A dictionary containing the result of the image generation.
            - "status" (str): "success" if the image was generated successfully.
            - "detail" (str): A message describing the result.
            - "file_name" (str): The filename of the generated image.
            - "image_bytes" (bytes): The binary content of the generated image.
            - "mime_type" (str): The MIME type of the generated image.
            Returns an empty dictionary if generation fails.
    """
    # Create a semaphore for this specific generation task to limit concurrency 
    # within this event loop context.
    image_semaphore = asyncio.Semaphore(get_image_generation_concurrency_limit())

    contents: List[Any] = [prompt]
    reference_images_for_eval = None

    if input_images:
        contents.extend(input_images)
        if input_image_descriptions and len(input_images) == len(input_image_descriptions):
            reference_images_for_eval = list(zip(input_images, input_image_descriptions))
        else:
            reference_images_for_eval = [(img, "Reference Image") for img in input_images]
        
    best_attempt = None
    best_eval = None

    for attempt_idx in range(get_image_generation_eval_reattempts() + 1):
        log_message(
            f"Generating image attempt {attempt_idx + 1} of {get_image_generation_eval_reattempts() + 1} eval attempts",
            Severity.INFO,
        )
        if attempt_idx > 0:
            log_message(
                f"Waiting for {get_image_generation_retry_delay_seconds()} seconds before retry...",
                Severity.INFO,
            )
            await asyncio.sleep(get_image_generation_retry_delay_seconds())

        result_bytes = None
        try:
            # We enforce the semaphore to prevent rate limits before yielding to adk_common
            async with image_semaphore:
                while True:
                    try:
                        extracted = await generate_image_bytes(
                            client=get_gemini_client(),
                            model=get_image_generation_model(),
                            contents=contents,
                            max_retries=get_image_generation_tenacity_attempts(),
                            retry_delay_min=1.0,
                            retry_delay_max=max(1.0, get_image_generation_retry_delay_seconds()),
                            aspect_ratio=aspect_ratio,
                        )
                        break
                    except api_exceptions.ResourceExhausted as e:
                        msg = "The model is facing extremely high traffic. Waiting 10 seconds before trying again..."
                        log_message(msg, Severity.WARNING)
                        if tool_context:
                            utils_agents.geminienterprise_print(tool_context, f"⚠️ {msg}")
                        await asyncio.sleep(10)
            if extracted:
                result_bytes = extracted[0][0]
                
        except ShowableException as e:
            # Fatal safety block or absolute unretriable error. Break EVAL loop immediately.
            log_message(f"Fatal generation error: {e.showable_message}", Severity.ERROR)
            return {
                "status": "failed",
                "detail": e.showable_message,
                "tool_context": tool_context,
            }
        except Exception as e:
            log_message(f"Unexpected Image generation error: {e}", Severity.ERROR)
            
        if not result_bytes:
            log_message(
                f"Image generation attempt {attempt_idx + 1} failed for prompt: '{prompt}'.",
                Severity.ERROR,
            )
            continue
            
        evaluation = None
        should_evaluate = get_image_generation_eval_reattempts() > 0
        if should_evaluate:
            log_message(f"Will run eval on generated image. Time: {datetime.datetime.now().strftime('%H:%M:%S.%f')}", Severity.DEBUG)
            evaluation = await evaluate_media(
                media_bytes=result_bytes,
                mime_type=IMAGE_MIME_TYPE,
                evaluation_criteria=prompt,
                allow_collage=allow_collage,
                reference_images=reference_images_for_eval,
            )

        # Track the best image across all failed attempts just in case we never pass
        if not best_eval or (evaluation and hasattr(evaluation, 'averaged_evaluation_score') and evaluation.averaged_evaluation_score > best_eval.averaged_evaluation_score):
            best_attempt = result_bytes
            best_eval = evaluation
            
        if not best_attempt and not best_eval:
             best_attempt = result_bytes
             best_eval = evaluation

        # Check if we passed or if evaluation was disabled
        if evaluation and evaluation.decision.lower() != "pass":
            improvement_prompt = f"An image was already generated and the evaluator deemed it did not pass quality and suggested the following to improve the image: {evaluation.improvement_prompt}"
            contents.append(improvement_prompt)

            log_message(
                f"{log_prefix} did not pass evaluation. Best Averaged Score: {best_eval.averaged_evaluation_score if best_eval else 'N/A'}.",
                Severity.WARNING,
            )

            log_message(
                f"Calling again with the following improvement prompt: `{improvement_prompt}`",
                Severity.WARNING,
            )
            continue
        else:
            # Passed the evaluation (or evaluation was skipped/EVAL=0)
            best_attempt = result_bytes
            best_eval = evaluation
            

            break

    extension = mimetypes.guess_extension(IMAGE_MIME_TYPE) or ".png"
    filename = f"{filename_without_extension}{extension}"
    
    if best_attempt:
        log_message(
            f"Image generated. Best averaged score: {best_eval.averaged_evaluation_score if best_eval and hasattr(best_eval, 'averaged_evaluation_score') else '[No Eval]'}. Filename: {filename}.",
            Severity.INFO,
        )
        return {
            "status": "success",
            "detail": f"Image generated successfully for {filename}.",
            "file_name": filename,
            "image_bytes": best_attempt,
            "mime_type": IMAGE_MIME_TYPE,
            "best_eval": best_eval,
            "tool_context": tool_context,
        }
    else:
        log_message(
            f"Failed to generate image after an initial attempt, {get_image_generation_eval_reattempts()} EVAL reattempts and {get_image_generation_tenacity_attempts()} TENACITY attempts",
            Severity.ERROR,
        )
        return {}