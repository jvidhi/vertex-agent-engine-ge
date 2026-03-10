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
"""Handles the generation of images based on storyline prompts."""

import asyncio
import json
import datetime
import random
import string
import time
from typing import Any, Dict, List, Optional, cast

from ad_generation_agent.utils import ad_generation_constants
from ad_generation_agent.utils.eval_result import EvalResult
from ad_generation_agent.utils.image_generation import (
    generate_and_select_best_image)

from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents, utils_gcs
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import (Severity, log_function_call,
                                            log_message)
from google import genai
from google.adk.tools.tool_context import ToolContext
from google.cloud import storage
from google.genai import types
from google.genai.types import HarmBlockThreshold, HarmCategory
from vertexai.preview.vision_models import ImageGenerationModel

GOOGLE_CLOUD_PROJECT = get_required_env_var("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")

RENDER_IMAGES_INLINE = get_required_env_var("RENDER_IMAGES_INLINE").lower() in ("true", "1", "yes")
RENDER_VIDEOS_INLINE = get_required_env_var("RENDER_VIDEOS_INLINE").lower() in ("true", "1", "yes")


# @log_function_call
async def _create_image_generation_task(
    scene_number: int,
    image_prompt: str,
    tool_context: ToolContext,
    is_logo_scene: bool = False,
    asset_sheet_uri: str | None = None,
    reference_images_uris: List[str] | None = None,
    product_image_uri: str | None = None,
    logo_image_uri: str | None = None,
    main_character_uri: str | None = None,
) -> Dict[str, Any]:
    """Creates a task for generating a single image.

    Args:
        scene_number (int): The scene number.
        image_prompt (str): The prompt for generating the image.
        tool_context (ToolContext): The tool context for saving artifacts.
        is_logo_scene (bool, optional): Whether the scene includes the logo.
        asset_sheet_uri (str, optional): URI of the asset sheet image.
        reference_images_uris (List[str], optional): URIs of reference images.
        product_image_uri (str, optional): URI of the product image.
        logo_image_uri (str, optional): URI of the logo image.
        main_character_uri (str, optional): URI of the main character image.

    Returns:
        Dict[str, Any]: A dictionary containing the result of the image generation.
            - "status" (str): "success" if the image was generated successfully.
            - "detail" (str): A message describing the result.
            - "file_name" (str): The filename of the generated image.
            - "image_bytes" (bytes): The binary content of the generated image.
            - "mime_type" (str): The MIME type of the generated image.
            Returns an empty dictionary if generation fails.
    """
    log_message(f"Generating image for scene {scene_number}: {image_prompt}", Severity.INFO)

    # Microsecond Timestamp + Random Chars
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
    
    filename_prefix = (
        f"{ad_generation_constants.SCENE_IMAGE_FILENAME_PREFIX}_{scene_number}_{timestamp_str}_{random_chars}"
    )

    reference_image_parts = []
    image_descriptions = []
    
    # Load Asset Sheet
    if asset_sheet_uri:
        try:
            asset_sheet_media = await utils_agents.load_resource(source_path=asset_sheet_uri, tool_context=tool_context)
            if asset_sheet_media and asset_sheet_media.media_bytes:
                part = types.Part.from_bytes(data=asset_sheet_media.media_bytes, mime_type=asset_sheet_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"ASSET SHEET: Follow the visual style and character look defined here ({asset_sheet_media.filename}).")
        except Exception as e:
            log_message(f"Failed to load asset sheet from {asset_sheet_uri}: {e}", Severity.WARNING)

    # Load Product Image
    if product_image_uri:
        try:
            product_media = await utils_agents.load_resource(source_path=product_image_uri, tool_context=tool_context)
            if product_media and product_media.media_bytes:
                part = types.Part.from_bytes(data=product_media.media_bytes, mime_type=product_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"PRODUCT IMAGE: This is the exact product ({product_media.filename}). It MUST be the central focus.")
        except Exception as e:
            log_message(f"Failed to load product image from {product_image_uri}: {e}", Severity.WARNING)

    # Load Logo Image
    if logo_image_uri:
        try:
            logo_media = await utils_agents.load_resource(source_path=logo_image_uri, tool_context=tool_context)
            if logo_media and logo_media.media_bytes:
                part = types.Part.from_bytes(data=logo_media.media_bytes, mime_type=logo_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"BRAND LOGO: This is the official logo ({logo_media.filename}). It MUST be clearly visible and undistorted.")
        except Exception as e:
            log_message(f"Failed to load logo image from {logo_image_uri}: {e}", Severity.WARNING)

    # Load Main Character Image
    if main_character_uri:
        try:
            char_media = await utils_agents.load_resource(source_path=main_character_uri, tool_context=tool_context)
            if char_media and char_media.media_bytes:
                part = types.Part.from_bytes(data=char_media.media_bytes, mime_type=char_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"MAIN CHARACTER: This is the main character ({char_media.filename}). Follow their look and style.")
        except Exception as e:
            log_message(f"Failed to load main character image from {main_character_uri}: {e}", Severity.WARNING)

    # Load other reference images array
    if reference_images_uris:
        for uri in reference_images_uris:
            generated_media: GeneratedMedia | None = await utils_agents.load_resource(
                source_path=uri, tool_context=tool_context
            )
            
            if generated_media and generated_media.media_bytes:
                part = types.Part.from_bytes(data=generated_media.media_bytes, mime_type=generated_media.mime_type)
                reference_image_parts.append((part))
                image_descriptions.append(f"REFERENCE IMAGE: Use this as general visual or stylistic reference ({generated_media.filename}).")

    logo_fidelity_protocol = ""
    if is_logo_scene:
        logo_fidelity_protocol = (
            "\n\n**CRITICAL LOGO FIDELITY PROTOCOL**:\n"
            "* This scene features the company logo. You **MUST** use the provided logo asset EXACTLY as is.\n"
            "* **DO NOT** alter, reimagine, or hallucinate the logo.\n"
            "* It must match the reference pixel-perfectly if possible."
        )
    
    attached_reference_images_text = ""
    if image_descriptions:
        attached_reference_images_text = "\n\n**ATTACHED REFERENCE IMAGES**:"
        for desc in image_descriptions:
            attached_reference_images_text += f"\n* {desc}"

    from adk_common.utils import utils_prompts

    variables = {
        "IMAGE_PROMPT": image_prompt,
        "LOGO_FIDELITY_PROTOCOL": logo_fidelity_protocol,
        "ATTACHED_REFERENCE_IMAGES": attached_reference_images_text
    }

    prompt = utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace=variables,
        filename="../prompts/scene_frame_generation_prompt.md"
    )

    return await generate_and_select_best_image(
        filename_without_extension=filename_prefix,
        input_images=reference_image_parts,
        prompt=prompt,
        tool_context=tool_context,
        log_prefix=f"Scene {scene_number}",
        input_image_descriptions=image_descriptions,
    )


# @log_function_call
async def generate_scene_frame(
    scene_number: int,
    prompt: str,
    tool_context: ToolContext,
    product_image_url: str = "",
    product_name: str = "",
    logo_image_url: str = "",
    main_character_url: str = "",
    asset_sheet_url: str = "",
    reference_images: List[str] | None = None,
    is_logo_scene: bool = False,
    healing_retry_count: int = 0,
) -> Dict[str, Any]:
    f"""Generates a single image for a commercial storyboard based on the provided parameters.

    Args:
        scene_number (int): The scene for which this image is being generated (starting from 1).
        prompt (str): A detailed image generation prompt for the scene.
            * Should describe in detail the scene.
            * Should be of a single location/setting. Avoid collages and multiple shots in a single video.
            * Character names won't be understood here, use pronouns + descriptions to detail actions.
            * Be VERY descriptive in what movements and camera angles you expect and what should not move in the scene. Describe who/what is causing the movement.
            * A video generation model will use this image as a starting point so consider how a subsequent video will move from it. Do not generate images that will lead to video inconsistencies.
            * For logo prompts ensure the logo is shown in a prominent position and that the outcome is 100% accurate. In this scenario, ensure you provide the logo as a reference image.
        tool_context (ToolContext): The context for artifact management.
        product_image_url (str, optional): The URL of the product image.
        product_name (str, optional): The name of the product.
        logo_image_url (str, optional): The URL of the logo image.
        main_character_url (str, optional): The URL of the main character reference image(s). This can contain multiple character references if needed.
        asset_sheet_url (str, optional): The URL of the asset sheet image.
        reference_images (List[str], optional): A list of URIs, URLs or filenames for all other images that should be used as reference.
        is_logo_scene (bool, optional): True if this scene features the company logo, False otherwise.
        healing_retry_count (int, optional): The current count of LLM healing attempts for this scene. Defaults to 0.

    Returns:
        Dict[str, Any]: A dictionary containing the status and details of the image generation process.
    """
    from adk_common.utils.utils_state import save_state_property
    from adk_common.utils.utils_agents import check_asset_exists
    from ad_generation_agent.utils import ad_generation_constants
    
    try:
        if reference_images is None:
            reference_images = []
            
        final_product_uri = product_image_url.strip()
        final_logo_uri = logo_image_url.strip()

        urls_to_validate = {
            ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL: final_product_uri,
            ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL: final_logo_uri,
            ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL: main_character_url,
            ad_generation_constants.STATE_KEY_ASSET_SHEET_URL: asset_sheet_url
        }
        
        invalid_urls = []
        for key, value in urls_to_validate.items():
            if value and isinstance(value, str) and value.strip():
                value = value.strip()
                exists, _ = check_asset_exists(value, set())
                if not exists:
                    invalid_urls.append(f"- {key}: {value}")
                    
        if invalid_urls:
            error_msg = (
                "The following URLs provided to the tool do not exist or are unreachable. "
                "Please check your memory or use `retrieve_generated_assets` to find the correct URLs. "
                "Do NOT guess or hallucinate URLs.\n" 
                + "\n".join(invalid_urls)
            )
            raise RuntimeError(error_msg)

        save_state_property(tool_context, ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL, final_product_uri)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_PRODUCT_NAME, product_name)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL, final_logo_uri)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL, main_character_url)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_ASSET_SHEET_URL, asset_sheet_url)
        
        all_refs = list(reference_images)


        result: Dict[str, Any] = await _create_image_generation_task(
            scene_number=scene_number,
            image_prompt=prompt,
            tool_context=tool_context,
            is_logo_scene=is_logo_scene,
            asset_sheet_uri=asset_sheet_url,
            reference_images_uris=all_refs,
            product_image_uri=final_product_uri,
            logo_image_uri=final_logo_uri,
            main_character_uri=main_character_url,
        )

        if result and result.get("status") == "success" and result.get("image_bytes"):
            generated_media = GeneratedMedia(
                filename=result["file_name"],
                mime_type=ad_generation_constants.IMAGE_MIMETYPE,
                media_bytes=result["image_bytes"],
            )

            generated_media = await utils_agents.save_to_artifact_and_render_asset(
                asset=generated_media,
                context=tool_context,
                save_in_gcs=True,
                save_in_artifacts=RENDER_IMAGES_INLINE,
                gcs_folder=utils_agents.get_or_create_unique_session_id(tool_context),
            )
    
            best_eval = result.get("best_eval")

            log_message(f"[generate_image_from_storyline_response] Image generation successful for scene: `{scene_number}`. New image URI: {generated_media.gcs_uri}", Severity.INFO)
            
            response = {
                "status": "success",
                "detail": "Image generation successful.",
                "scene_number": scene_number,
                "generated_image_uri": generated_media.gcs_uri,
            }
            
            if best_eval is not None:
                response["evaluation_decision"] = getattr(best_eval, "decision", "Pass")
                response["evaluation_score"] = getattr(best_eval, "averaged_evaluation_score", 10.0)
                response["evaluation_feedback"] = getattr(best_eval, "improvement_prompt", "Looks great.")
                
            response["healing_retry_count"] = healing_retry_count
            if healing_retry_count >= 2 and response.get("evaluation_decision") != "Pass":
                response["evaluation_decision"] = "Forced Pass (Max Retries Exceeded)"
                response["system_warning"] = "MAXIMUM RECOVERY COUNT EXCEEDED. DO NOT RETRY THIS SCENE AGAIN. YOU MUST ACCEPT THIS ASSET AS IS."
                
            return response
           
        else:
            result_string = json.dumps(result) if result else "None"
            log_message(f"[generate_image_from_storyline_response] Failed to save generated image for scene: `{scene_number}`. Result: {result_string}", Severity.ERROR)
            response = {"status": "failed", "detail": "Image generation failed."}
            return response

    except Exception as e:
        log_message(f"Error in generate_image_from_storyline: {e}", Severity.ERROR)
        raise e
