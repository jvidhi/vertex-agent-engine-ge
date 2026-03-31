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
"""Handles the generation of final display ads."""

import datetime
import json
import random
import re
import string
import time
from typing import Any, Dict, List, Optional, cast

from ad_generation_agent.utils.eval_result import EvalResult
from ad_generation_agent.utils.image_generation import (
    generate_and_select_best_image)
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import (Severity, log_message, log_function_call)
from google.adk.tools.tool_context import ToolContext
from google.genai import types


IMAGE_DEFAULT_ASPECT_RATIO = get_required_env_var("IMAGE_DEFAULT_ASPECT_RATIO")
RENDER_IMAGES_INLINE = get_required_env_var("RENDER_IMAGES_INLINE").lower() in ("true", "1", "yes")
RENDER_VIDEOS_INLINE = get_required_env_var("RENDER_VIDEOS_INLINE").lower() in ("true", "1", "yes")


async def _create_display_ad_task(
    prompt_description: str,
    tool_context: ToolContext,
    concept_keywords: str,
    asset_sheet_uri: str | None = None,
    reference_images_uris: List[str] | None = None,
    product_image_uri: str | None = None,
    logo_image_uri: str | None = None,
    main_character_uri: str | None = None,
    aspect_ratio: str | None = None,
) -> Dict[str, Any]:
    """Creates a task for generating a single display ad.

    Args:
        prompt_description (str): The visual description/concept for the ad.
        tool_context (ToolContext): The tool context for saving artifacts.
        concept_keywords (str): Short descriptive keywords for the filename.
        asset_sheet_uri (str, optional): URI of the asset sheet image.
        reference_images_uris (List[str], optional): URIs of reference images.
        product_image_uri (str, optional): URI of the product image.
        logo_image_uri (str, optional): URI of the logo image.
        main_character_uri (str, optional): URI of the main character image.

    Returns:
        Dict[str, Any]: A dictionary containing the result of the image generation.
    """
    log_message(f"Generating display ad for prompt: {prompt_description}", Severity.INFO)

    # Sanitize Concept Keywords
    # Allow alphanumeric and replace spaces/others with underscores
    sanitized_keywords = re.sub(r'[^a-zA-Z0-9]', '_', concept_keywords).strip('_')
    if not sanitized_keywords:
        sanitized_keywords = "generic"
    
    # Collapse multiple underscores
    sanitized_keywords = re.sub(r'_+', '_', sanitized_keywords)

    # 1. Microsecond Timestamp
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")

    # 2. Random 3 Characters
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))

    # Construct Filename
    filename_prefix = f"display_ad_{sanitized_keywords}_{timestamp_str}_{random_chars}"

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

    # Load other reference images (products, logos, etc.)
    if reference_images_uris:
        for uri in reference_images_uris:
            generated_media: GeneratedMedia | None = await utils_agents.load_resource(
                source_path=uri, tool_context=tool_context
            )
            
            if generated_media and generated_media.media_bytes:
                part = types.Part.from_bytes(data=generated_media.media_bytes, mime_type=generated_media.mime_type)
                reference_image_parts.append((part))
                image_descriptions.append(f"REFERENCE: {generated_media.filename}")

    image_context_text = ""
    if image_descriptions:
        image_context_text = "\n**IMAGE CONTEXT**:"
        for desc in image_descriptions:
            image_context_text += f"\n* {desc}"
            
    # IMPORTANT (FIX): Enforce the default aspect ratio if none is explicitly requested by the agent
    final_aspect_ratio = aspect_ratio or IMAGE_DEFAULT_ASPECT_RATIO

    from adk_common.utils import utils_prompts
    final_prompt = utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace={
            "PROMPT_DESCRIPTION": prompt_description,
            "ASPECT_RATIO": final_aspect_ratio,
            "IMAGE_CONTEXT": image_context_text
        },
        filename="../prompts/display_ad_generation_prompt.md"
    )

    return await generate_and_select_best_image(
        filename_without_extension=filename_prefix,
        input_images=reference_image_parts,
        prompt=final_prompt,
        aspect_ratio=final_aspect_ratio,
    )


@log_function_call
async def generate_display_ad(
    prompt: str,
    tool_context: ToolContext,
    concept_keywords: str = "ad",
    product_image_url: str = "",
    product_name: str = "",
    logo_image_url: str = "",
    main_character_url: str = "",
    asset_sheet_url: str = "",
    reference_images: List[str] | None = None,
    healing_retry_count: int = 0,
    aspect_ratio: str | None = None,
) -> Dict[str, Any]:
    f"""Generates a final, high-quality Display Ad (image) with short copy and branding.
    
    Use this tool ONLY when the user specifically requests a "Display Ad", "Image Ad", or "Static Ad".
    Do NOT use this for video generation steps.

    Args:
        prompt (str): A detailed description of the ad concept, mood, and any specific copy text requested.
        concept_keywords (str, optional): Short descriptive keywords (1-3 words) to identify the ad concept in the filename. Defaults to "ad".
        product_image_url (str, optional): A URI or path to the canonical product image. Defaults to "".
        product_name (str, optional): The name of the product to search in the catalog if no reference URI is provided. Defaults to empty string.
        logo_image_url (str, optional): A URI or path to the brand logo. Defaults to "".
        main_character_url (str, optional): The URL of the main character reference image(s). This can contain multiple character references if needed.
        asset_sheet_url (str, optional): The URL of the asset sheet image.
        reference_images (List[str], optional): List of URIs for additional reference images. Defaults to None.
        healing_retry_count (int, optional): The current count of LLM healing attempts for this ad. Defaults to 0.
        aspect_ratio (str, optional): The desired aspect ratio (e.g., "16:9", "1:1"). If not provided, it falls back to the workspace default (IMAGE_DEFAULT_ASPECT_RATIO).

    Returns:
        Dict[str, Any]: A dictionary containing the status and details of the generated ad.
    """
    from adk_common.utils.utils_state import save_state_property
    from adk_common.utils.utils_agents import check_asset_exists
    from ad_generation_agent.utils import ad_generation_constants
    
    try:
        if reference_images is None:
            reference_images = []
            
        utils_agents.geminienterprise_print(tool_context, "Generating Display Ad...")
        
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
            return {
                "status": "failed",
                "detail": error_msg
            }

        save_state_property(tool_context, ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL, final_product_uri)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_PRODUCT_NAME, product_name)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL, final_logo_uri)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL, main_character_url)
        save_state_property(tool_context, ad_generation_constants.STATE_KEY_ASSET_SHEET_URL, asset_sheet_url)
        
        log_message(f"Using Product URI: {final_product_uri}", Severity.INFO)
        log_message(f"Using Logo URI: {final_logo_uri}", Severity.INFO)

        all_refs = list(reference_images)

        result: Dict[str, Any] = await _create_display_ad_task(
            prompt_description=prompt,
            asset_sheet_uri=asset_sheet_url,
            reference_images_uris=all_refs,
            product_image_uri=final_product_uri,
            logo_image_uri=final_logo_uri,
            main_character_uri=main_character_url,
            tool_context=tool_context,
            concept_keywords=concept_keywords,
            aspect_ratio=aspect_ratio,
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
                gcs_folder=f"{ad_generation_constants.SESSIONS_PREFIX}/{utils_agents.get_or_create_unique_session_id(tool_context)}",
            )

            best_eval = result.get("best_eval")

            response = {
                "status": "success",
                "detail": "Display Ad generated successfully.",
                "generated_image_uri": generated_media.gcs_uri,
            }
            
            if best_eval is not None:
                response["evaluation_decision"] = getattr(best_eval, "decision", "Pass")
                response["evaluation_score"] = getattr(best_eval, "averaged_evaluation_score", 10.0)
                response["evaluation_feedback"] = getattr(best_eval, "improvement_prompt", "Looks great.")
                
            response["healing_retry_count"] = healing_retry_count
            if healing_retry_count >= 2 and response.get("evaluation_decision") != "Pass":
                response["evaluation_decision"] = "Forced Pass (Max Retries Exceeded)"
                response["system_warning"] = "MAXIMUM RECOVERY COUNT EXCEEDED. DO NOT RETRY THIS DISPLAY AD AGAIN. YOU MUST ACCEPT THIS ASSET AS IS."
                
            log_message(f"[generate_display_ad] Success. response: {response}", Severity.INFO)
            return response
           
        else:
            result_string = json.dumps(result) if result else "None"
            log_message(f"[generate_display_ad] Failed to save image. Result: {result_string}", Severity.ERROR)
            return {"status": "failed", "detail": "Display Ad generation failed."}

    except Exception as e:
        error_msg = f"Error in generate_display_ad: {str(e)}"
        log_message(error_msg, Severity.ERROR)
        return {
            "status": "failed", 
            "detail": error_msg,
            "system_instruction": "Display Ad generation failed. Do NOT crash. Gracefully inform the user."
        }
