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
"""Handles the generation of asset sheets."""

import asyncio
import datetime
import json
import random
import string
from typing import Any, Dict, List, cast

from ad_generation_agent.utils import ad_generation_constants
from ad_generation_agent.utils.eval_result import EvalResult
from ad_generation_agent.utils.image_generation import \
    generate_and_select_best_image
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.utils_logging import (Severity, log_message)
from google.adk.tools.tool_context import ToolContext
from google.api_core import exceptions as api_exceptions
from google.genai import types

from adk_common.utils.constants import get_required_env_var
RENDER_IMAGES_INLINE = get_required_env_var("RENDER_IMAGES_INLINE").lower() in ("true", "1", "yes")

# @log_function_call
async def generate_asset_sheet(
    storyline: str,
    tool_context: ToolContext,
    visual_style_guide: str = "",
    product_name: str = "",
    product_image_reference: str = "",
    prompt: str = "",
    brand_guidelines: str = "",
    main_character_url: str = "",
    reference_images: List[str] = [],
    previous_asset_sheet_uri: str = "",
    logo_image_uri: str = "",
) -> Dict[str, Any]:
    """Generates a visual asset sheet for a marketing campaign.

    This tool creates a comprehensive visual guide (an "Asset Sheet"), a single
    comprehensive artifact that establishes the core visual elements for the campaign.
    It includes the hero character, key locations, color palette, and overall style.

    Args:
        product_name (str): The name of the product being advertised.
        storyline (str): The complete narrative arc and script of the commercial.
        visual_style_guide (str, optional): Free-form text describing specific characters or locations. Defaults to "".
        prompt (str, optional): Additional specific instructions or constraints for the
            asset sheet generation. Defaults to "".
        brand_guidelines (str, optional): A comprehensive string detailing the brand's
            voice, core values, and visual identity standards. Defaults to "".
        main_character_url (str, optional): A URI or path to the canonical main character image.
            Defaults to "".
        product_image_reference (str, optional): A URI or path to the canonical product image.
            Defaults to "".
        reference_images (List[str], optional): A list of URIs or paths to additional
            reference imagery (e.g., style examples, competitors). Defaults to [].
        previous_asset_sheet_uri (str, optional): A URI or path to a previously generated
            asset sheet that should be used as a base for refinement. Defaults to "".
        logo_image_uri (str, optional): A URI or path to the brand logo. Defaults to "".

    Returns:
        Dict[str, Any]: The result containing the asset sheet filename and URI.
    """
    from adk_common.utils.utils_state import save_state_property
    from adk_common.utils.utils_agents import check_asset_exists

    try:
        utils_agents.geminienterprise_print(tool_context, f"Generating asset sheet for {product_name}...")
        
        if reference_images is None:
            reference_images = []

        final_product_uri = product_image_reference.strip() if product_image_reference else ""
        final_logo_uri = logo_image_uri.strip() if logo_image_uri else ""
        final_prev_sheet = previous_asset_sheet_uri.strip() if previous_asset_sheet_uri else ""
        final_character_uri = main_character_url.strip() if main_character_url else ""

        urls_to_validate = {
            ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL: final_product_uri,
            ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL: final_logo_uri,
            ad_generation_constants.STATE_KEY_ASSET_SHEET_URL: final_prev_sheet,
            ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL: final_character_uri
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
        if final_prev_sheet:
             save_state_property(tool_context, ad_generation_constants.STATE_KEY_ASSET_SHEET_URL, final_prev_sheet)
        if final_character_uri:
             save_state_property(tool_context, ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL, final_character_uri)

        result = await _create_asset_sheet_generation_task(
            storyline=storyline,
            visual_style_guide=visual_style_guide,
            tool_context=tool_context,
            additional_instructions=prompt,
            brand_guidelines=brand_guidelines,
            product_image_reference=final_product_uri,
            logo_image_uri=final_logo_uri,
            main_character_url=final_character_uri,
            reference_images_uris=reference_images,
            previous_asset_sheet_uri=final_prev_sheet
        )

        if result and result.get("status") == "success" and result.get("image_bytes"):
            generated_media = GeneratedMedia(
                media_bytes=result["image_bytes"],
                filename=result["file_name"],
                mime_type=result["mime_type"],
            )

            generated_media = await utils_agents.save_to_artifact_and_render_asset(
                asset=generated_media,
                context=tool_context,
                save_in_gcs=True,
                save_in_artifacts=RENDER_IMAGES_INLINE,
                gcs_folder=utils_agents.get_or_create_unique_session_id(tool_context),
            )
            
            description_parts = [
                f"Product Name: {product_name}",
                f"Storyline: {storyline}",
                f"Visual Style Guide: {visual_style_guide}",
                f"Brand Guidelines: {brand_guidelines}",
            ]
            if prompt:
                description_parts.append(f"Additional Instructions: {prompt}")
            if product_image_reference:
                description_parts.append(f"Product Image Reference: {product_image_reference}")
            if final_character_uri:
                description_parts.append(f"Main Character URL: {final_character_uri}")
            if reference_images:
                description_parts.append(f"Reference Images: {', '.join(reference_images)}")
            if final_logo_uri:
                 description_parts.append(f"Logo Image: {final_logo_uri}")

            utils_agents.geminienterprise_print(tool_context, "Asset sheet generation complete.")

            response = {
                "status": "success",
                "asset_sheet_filename": generated_media.filename,
                "asset_sheet_gcs_uri": generated_media.gcs_uri,
                "generation_details": json.dumps(description_parts),
            }
            log_message(f"[generate_asset_sheet_response] {response}", Severity.INFO)
            return response
        else:
            response = {
                "status": "failed",
                "detail": "Failed to generate asset sheet image.",
            }
            log_message(f"[generate_asset_sheet_response] {response}", Severity.ERROR)
            return response

    except Exception as e:
        log_message(f"Error in generate_asset_sheet: {e}", Severity.ERROR)
        raise e


# @log_function_call
def _create_asset_sheet_prompt(
    storyline: str,
    visual_style_guide: str,
    additional_instructions: str,
    reference_images_descriptions: list[str],
    brand_guidelines: str = "",
) -> str:
    """Creates the prompt for the asset sheet image."""
    
    visual_style_guide_text = ""
    if visual_style_guide:
        visual_style_guide_text = f"\n# Visual Style Guide:\n{visual_style_guide}\n"

    additional_instructions_bullet = ""
    reference_images_list = ""
    brand_guidelines_text = ""

    if brand_guidelines:
        brand_guidelines_text = f"\n# Brand Guidelines:\n{brand_guidelines}\n\nEnsure the asset sheet strictly adheres to these brand guidelines, especially regarding color palette and visual identity."

    if reference_images_descriptions:
        additional_instructions_bullet = (
            "# The user provided images that you **MUST** leverage"
        )
        for image_description in reference_images_descriptions:
            reference_images_list += f"\n* {image_description}"

    from adk_common.utils import utils_prompts

    variables = {
        "VISUAL_STYLE_GUIDE": visual_style_guide_text,
        "BRAND_GUIDELINES": brand_guidelines_text,
        "ADDITIONAL_INSTRUCTIONS_BULLET": additional_instructions_bullet,
        "REFERENCE_IMAGES_LIST": reference_images_list,
        "ADDITIONAL_INSTRUCTIONS": additional_instructions,
        "STORYLINE": storyline
    }

    return utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace=variables,
        filename="../prompts/asset_sheet_generation_prompt.md"
    )


# @log_function_call
async def _create_asset_sheet_generation_task(
    storyline: str,
    visual_style_guide: str,
    tool_context: ToolContext,
    additional_instructions: str,
    brand_guidelines: str = "",
    product_image_reference: str | None = None,
    logo_image_uri: str | None = None,
    main_character_url: str | None = None,
    reference_images_uris: List[str] | None = None,
    previous_asset_sheet_uri: str | None = None,
) -> Dict[str, Any] | None:
    """Generates and evaluates asset sheet images, saving the best one.

    Args:
        storyline (str): The storyline arc.
        visual_style_guide (str): Free-form string dictating visuals or characters.
        tool_context (ToolContext): The context for saving artifacts.
        additional_instructions (str): Additional instructions provided by the user or the agent.
        brand_guidelines (str): The guidelines around brand palette and mood to be injected.
        product_image_reference (str | None): The product image reference.
        logo_image_uri (str | None): The location of the official brand logo.
        main_character_url (str | None): The main character visual reference.
        reference_images_uris (List[str] | None): Other general style references.
        previous_asset_sheet_uri (str | None): A previously generated asset sheet for edits.

    Returns:
        Dict[str, Any] containing the `generate_and_select_best_image` results.
    """
    utils_agents.geminienterprise_print(tool_context, "Generating asset sheet image...")

    reference_image_parts = []
    image_descriptions = []
    
    # Load product image
    if product_image_reference:
        try:
            product_media = await utils_agents.load_resource(source_path=product_image_reference, tool_context=tool_context)
            if product_media and product_media.media_bytes:
                part = types.Part.from_bytes(data=product_media.media_bytes, mime_type=product_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"PRODUCT IMAGE: Use this exact product.")
        except Exception as e:
            log_message(f"Failed to load product photo from {product_image_reference}: {e}", Severity.WARNING)

    # Load logo
    if logo_image_uri:
        try:
            logo_media = await utils_agents.load_resource(source_path=logo_image_uri, tool_context=tool_context)
            if logo_media and logo_media.media_bytes:
                part = types.Part.from_bytes(data=logo_media.media_bytes, mime_type=logo_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"BRAND LOGO: This is the official brand logo ({logo_media.filename}). It must be clearly visible.")
        except Exception as e:
            log_message(f"Failed to load logo from {logo_image_uri}: {e}", Severity.WARNING)

    # Load main character
    if main_character_url:
        try:
            char_media = await utils_agents.load_resource(source_path=main_character_url, tool_context=tool_context)
            if char_media and char_media.media_bytes:
                part = types.Part.from_bytes(data=char_media.media_bytes, mime_type=char_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"MAIN CHARACTER: Make sure the protagonist in the asset sheet exactly matches this person ({char_media.filename}). Maintain consistency for face, age, and style.")
        except Exception as e:
            log_message(f"Failed to load main character from {main_character_url}: {e}", Severity.WARNING)

    # Load reference images
    if reference_images_uris:
        for ref_uri in reference_images_uris:
            try:
                 ref_media = await utils_agents.load_resource(source_path=ref_uri, tool_context=tool_context)
                 if ref_media and ref_media.media_bytes:
                    part = types.Part.from_bytes(data=ref_media.media_bytes, mime_type=ref_media.mime_type)
                    reference_image_parts.append(part)
                    image_descriptions.append(f"Reference image: {ref_media.filename}")
            except Exception as e:
                log_message(f"Failed to load reference image from {ref_uri}: {e}", Severity.WARNING)

    # Load previous asset sheet
    if previous_asset_sheet_uri:
        try:
             prev_media = await utils_agents.load_resource(source_path=previous_asset_sheet_uri, tool_context=tool_context)
             if prev_media and prev_media.media_bytes:
                part = types.Part.from_bytes(data=prev_media.media_bytes, mime_type=prev_media.mime_type)
                reference_image_parts.append(part)
                image_descriptions.append(f"PREVIOUS ASSET SHEET: This is the existing asset sheet ({prev_media.filename}). Refine this specific image based on the user's new instructions. Maintain what wasn't asked to change.")
        except Exception as e:
            log_message(f"Failed to load previous asset sheet from {previous_asset_sheet_uri}: {e}", Severity.WARNING)

    image_prompt = _create_asset_sheet_prompt(
        storyline=storyline,
        visual_style_guide=visual_style_guide,
        additional_instructions=additional_instructions,
        reference_images_descriptions=image_descriptions,
        brand_guidelines=brand_guidelines,
    )
    
    log_message(
        f"Generating asset sheet image for prompt: '{image_prompt}'", Severity.INFO
    )

    # Microsecond Timestamp + Random Chars
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
    random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
    
    return await generate_and_select_best_image(
        filename_without_extension=f"asset_sheet_{timestamp_str}_{random_chars}",
        prompt=image_prompt,
        input_images=reference_image_parts,
        allow_collage=True,
    )
