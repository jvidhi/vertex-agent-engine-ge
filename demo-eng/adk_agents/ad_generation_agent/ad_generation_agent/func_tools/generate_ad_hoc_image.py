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
"""Handles the generation of ad-hoc, standalone images."""

import asyncio
import json
import datetime
import random
import string
import time
from typing import Any, Dict, List, Optional, cast

from ad_generation_agent.utils import ad_generation_constants
from ad_generation_agent.utils.eval_result import EvalResult
from ad_generation_agent.utils.image_generation import generate_and_select_best_image

from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents, utils_gcs
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import Severity, log_function_call, log_message
from google import genai
from google.adk.tools.tool_context import ToolContext
from google.cloud import storage
from google.genai import types

GOOGLE_CLOUD_PROJECT = get_required_env_var("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")

RENDER_IMAGES_INLINE = get_required_env_var("RENDER_IMAGES_INLINE").lower() in ("true", "1", "yes")

async def _create_ad_hoc_image_generation_task(
    image_prompt: str,
    tool_context: ToolContext,
    is_logo_scene: bool = False,
    asset_sheet_uri: str | None = None,
    reference_images_uris: List[str] | None = None,
    product_image_uri: str | None = None,
    logo_image_uri: str | None = None,
    main_character_uri: str | None = None,
) -> Dict[str, Any]:
    """Creates a task for generating an ad-hoc image.

    Args:
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
    """
    log_message(f"Generating ad-hoc image: {image_prompt}", Severity.INFO)

    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
    
    filename_prefix = f"adhoc_image_{timestamp_str}_{random_chars}"

    reference_image_parts = []
    image_descriptions = []
    
    if asset_sheet_uri:
        try:
            asset_sheet_media = await utils_agents.load_resource(source_path=asset_sheet_uri, tool_context=tool_context)
            if asset_sheet_media and asset_sheet_media.media_bytes:
                part = types.Part.from_bytes(data=asset_sheet_media.media_bytes, mime_type=asset_sheet_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"ASSET SHEET: Follow the visual style and character look defined here ({asset_sheet_media.filename}).")
        except Exception as e:
            log_message(f"Failed to load asset sheet from {asset_sheet_uri}: {e}", Severity.WARNING)

    if product_image_uri:
        try:
            product_media = await utils_agents.load_resource(source_path=product_image_uri, tool_context=tool_context)
            if product_media and product_media.media_bytes:
                part = types.Part.from_bytes(data=product_media.media_bytes, mime_type=product_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"PRODUCT IMAGE: This is the exact product ({product_media.filename}). It MUST be the central focus.")
        except Exception as e:
            log_message(f"Failed to load product image from {product_image_uri}: {e}", Severity.WARNING)

    if logo_image_uri:
        try:
            logo_media = await utils_agents.load_resource(source_path=logo_image_uri, tool_context=tool_context)
            if logo_media and logo_media.media_bytes:
                part = types.Part.from_bytes(data=logo_media.media_bytes, mime_type=logo_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"BRAND LOGO: This is the official logo ({logo_media.filename}). It MUST be clearly visible and undistorted.")
        except Exception as e:
            log_message(f"Failed to load logo image from {logo_image_uri}: {e}", Severity.WARNING)

    if main_character_uri:
        try:
            char_media = await utils_agents.load_resource(source_path=main_character_uri, tool_context=tool_context)
            if char_media and char_media.media_bytes:
                part = types.Part.from_bytes(data=char_media.media_bytes, mime_type=char_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"MAIN CHARACTER: This is the main character ({char_media.filename}). Follow their look and style.")
        except Exception as e:
            log_message(f"Failed to load main character image from {main_character_uri}: {e}", Severity.WARNING)

    if reference_images_uris:
        for uri in reference_images_uris:
            generated_media: GeneratedMedia | None = await utils_agents.load_resource(source_path=uri, tool_context=tool_context)
            if generated_media and generated_media.media_bytes:
                part = types.Part.from_bytes(data=generated_media.media_bytes, mime_type=generated_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"REFERENCE IMAGE: Use this as general visual or stylistic reference ({generated_media.filename}).")

    prompt = f"{image_prompt}"
    prompt += "\n\n**STYLE MANDATE**:"
    prompt += "\n* Ensure the image generated follows all guidance provided in the asset sheet, most especially product & character consistency."
    prompt += "\n* Unless a specific art style is requested in the prompt, the image MUST be high-quality and visually striking."
    
    prompt += "\n\n**CONSISTENCY PROTOCOL**:"
    prompt += "\n* You **MUST** use the attached reference images to maintain consistent character appearance and product details."
    prompt += "\n* If the character's face is visible, it MUST match the reference image exactly."
    
    if is_logo_scene:
        prompt += "\n\n**CRITICAL LOGO FIDELITY PROTOCOL**:"
        prompt += "\n* This image features the company logo. You **MUST** use the provided logo asset EXACTLY as is."
        prompt += "\n* **DO NOT** alter, reimagine, or hallucinate the logo."
    
    if image_descriptions:
        prompt += "\n\n**ATTACHED REFERENCE IMAGES**:"
        for desc in image_descriptions:
            prompt += f"\n* {desc}"

    return await generate_and_select_best_image(
        filename_without_extension=filename_prefix,
        input_images=reference_image_parts,
        prompt=prompt,
    )


# @log_function_call
async def generate_ad_hoc_image(
    prompt: str,
    tool_context: ToolContext,
    product_image_url: str = "",
    product_name: str = "",
    logo_image_url: str = "",
    main_character_url: str = "",
    asset_sheet_url: str = "",
    reference_images: List[str] | None = None,
    is_logo_scene: bool = False,
) -> Dict[str, Any]:
    f"""Generates a standalone, custom image based on user requests (ad-hoc imagery).

    Args:
        prompt (str): A detailed image generation prompt.
        tool_context (ToolContext): The context for artifact management.
        product_image_url (str, optional): The URL of the product image.
        product_name (str, optional): The name of the product.
        logo_image_url (str, optional): The URL of the logo image.
        main_character_url (str, optional): The URL of the main character reference image(s).
        asset_sheet_url (str, optional): The URL of the asset sheet image.
        reference_images (List[str], optional): A list of URIs for all other images that should be used as reference.
        is_logo_scene (bool, optional): True if this image should prominently feature the company logo, False otherwise.

    Returns:
        Dict[str, Any]: A dictionary containing the status and details of the image generation.
    """
    from adk_common.utils.utils_state import save_state_property
    from adk_common.utils.utils_agents import check_asset_exists
    
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

        result: Dict[str, Any] = await _create_ad_hoc_image_generation_task(
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
    
            log_message(f"[generate_ad_hoc_image] Image generation successful. New image URI: {generated_media.gcs_uri}", Severity.INFO)
            return {
                "status": "success",
                "detail": "Ad-Hoc image generation successful.",
                "generated_image_uri": generated_media.gcs_uri,
            }
           
        else:
            result_string = json.dumps(result) if result else "None"
            log_message(f"[generate_ad_hoc_image] Failed to save generated image. Result: {result_string}", Severity.ERROR)
            response = {"status": "failed", "detail": "Image generation failed."}
            return response

    except Exception as e:
        log_message(f"Error in generate_ad_hoc_image: {e}", Severity.ERROR)
        raise e
