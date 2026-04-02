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
"""Generates video clips from images using Google's Vertex AI services."""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from google.adk.tools.tool_context import ToolContext
from google.genai.types import Image as GenImage
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import Severity, log_function_call, log_message

from google.genai.types import GenerateContentConfig
from google.genai import types
from ad_generation_agent.utils.image_generation import get_gemini_client

from ad_generation_agent.utils.video_generation import (
    VideoGenerationInput,
    generate_single_video,
    generate_single_video_from_ingredients,
)

import json
import asyncio
from typing import Dict, Any, List

from adk_common.utils.utils_logging import Severity, log_message
from adk_common.utils import utils_agents
from google.adk.tools.tool_context import ToolContext

VIDEO_GENERATION_CONCURRENCY_LIMIT = int(get_required_env_var("VIDEO_GENERATION_CONCURRENCY_LIMIT"))
VIDEO_DEFAULT_DURATION = int(get_required_env_var("VIDEO_DEFAULT_DURATION"))
VIDEO_DEFAULT_ASPECT_RATIO = get_required_env_var("VIDEO_DEFAULT_ASPECT_RATIO")
LLM_GEMINI_MODEL_ADGEN_SUBCALLS = get_required_env_var("LLM_GEMINI_MODEL_ADGEN_SUBCALLS") 

async def _enhance_prompt_with_llm(
    raw_prompt: str, is_logo_scene: bool, tool_context: ToolContext
) -> str:
    """Uses an LLM (Director's Mode) to expand a simple prompt into a detailed visual brief."""
    from adk_common.utils import utils_prompts

    try:
        utils_agents.geminienterprise_print(tool_context, "Refining video prompt with Director's Agent...")
        
        composition_guide = "Wide cinematic landscape composition"
        if "9:16" in VIDEO_DEFAULT_ASPECT_RATIO:
            composition_guide = "Vertical, social-media focused composition (tall frame)"
        elif "1:1" in VIDEO_DEFAULT_ASPECT_RATIO:
            composition_guide = "Square composition, centered subject"
            
        is_logo_scene_text = "YES - This scene contains a corporate logo." if is_logo_scene else "NO."

        variables = {
            "VIDEO_DEFAULT_ASPECT_RATIO": VIDEO_DEFAULT_ASPECT_RATIO,
            "COMPOSITION_GUIDE": composition_guide,
            "IS_LOGO_SCENE_TEXT": is_logo_scene_text,
            "RAW_PROMPT": raw_prompt
        }

        system_instruction = utils_prompts.load_prompt_file_from_calling_agent(
            variables_to_replace=variables,
            filename="../prompts/video_director_enhancement_prompt.md"
        )

        vertex_client = get_gemini_client()
        response = await vertex_client.aio.models.generate_content(
            model=LLM_GEMINI_MODEL_ADGEN_SUBCALLS,
            contents=[prompt],
            config=GenerateContentConfig(
                temperature=0.7,
                thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=32000) if "thinking" in LLM_GEMINI_MODEL_ADGEN_SUBCALLS.lower() else None
            ),
        )

        
        if response.text:
            log_message(f"Enhanced Prompt: {response.text}", Severity.INFO)
            return _construct_technical_prompt(response.text, is_logo_scene)
            
        return _construct_technical_prompt(raw_prompt, is_logo_scene)
        
    except Exception as e:
        log_message(f"Prompt enhancement failed, using raw prompt. Error: {e}", Severity.WARNING)
        return _construct_technical_prompt(raw_prompt, is_logo_scene)


def _construct_technical_prompt(enhanced_description: str, is_logo_scene: bool) -> str:
    """Wraps the enhanced description in a strict technical container to enforce physics and camera mandates."""
    from adk_common.utils import utils_prompts
    
    logo_instruction = ""
    if is_logo_scene:
        logo_instruction = (
            "LOGO PROTOCOL: The logo in the scene is a rigid, static asset. "
            "It must not warp, bend, or dissolve. It must match the reference pixels exactly."
        )

    variables = {
        "LOGO_INSTRUCTION": logo_instruction,
        "ENHANCED_DESCRIPTION": enhanced_description
    }

    final_prompt = utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace=variables,
        filename="../prompts/video_technical_mandates_prompt.md"
    )
    
    return final_prompt


@log_function_call
async def generate_video_from_first_frame(
    scene_number: int,
    prompt: str,
    reference_image: str,
    is_logo_scene: bool,
    duration_seconds: int,
    tool_context: ToolContext,
    product_image_url: str = "",
    product_name: str = "",
    logo_image_url: str = "",
    main_character_url: str = "",
    asset_sheet_url: str = "",
    reference_images: List[str] | None = None,
    healing_retry_count: int = 0,
    aspect_ratio: str | None = None,
) -> Dict[str, Any]:
    f"""Generates a single video clip based on the provided parameters.

    Args:
        scene_number (int): The sequential number of the scene (starting from 1).
        prompt (str): A detailed video generation prompt for the scene.
            * Should describe the motion and events for each scene.
            * Should only describe a 4 second scene, so describe a quick scene with only one setting.
            * Should be of a single take in a single location. Avoid collages and multiple shots in a single video.
            * Character names won't be understood here, use pronouns + descriptions to detail actions.
            * Be VERY descriptive in what movements and camera angles you expect and what should not move in the scene. Describe who/what is causing the movement.
            * The video generation model will use this image as a starting point. Be clear about how the scene transitions and keep it on theme.
            * Explicitly ground each of your prompts to follow the laws of physics.
        reference_image (str): The exact GCS URI (e.g., "gs://bucket/image.png") of the reference image to use.
        is_logo_scene (bool): True if this scene features the company logo, False otherwise.
        duration_seconds (int): The desired duration of the scene in seconds. Default is {VIDEO_DEFAULT_DURATION}.
        tool_context (ToolContext): The context for artifact management.
        product_image_url (str, optional): A URI or path to the canonical product image.
        product_name (str, optional): The name of the product to search in the catalog if no reference URI is provided.
        healing_retry_count (int, optional): The current count of LLM healing attempts for this scene. Defaults to 0.
        logo_image_url (str, optional): A URI or path to the brand logo.
        main_character_url (str, optional): The URL of the main character reference image(s). This can contain multiple character references if needed.
        asset_sheet_url (str, optional): The URL of the asset sheet image.
        reference_images (List[str], optional): List of URIs for additional reference images. Not used directly by the video model but tracked for state.
        aspect_ratio (str, optional): The target aspect ratio for the generated video (e.g. \"16:9\"). If none, defaults to runtime env.

    Returns:
        Dict[str, Any]: A dictionary containing the status and details of the video generation process.
    """
    from adk_common.utils.utils_state import save_state_property
    from adk_common.utils.utils_agents import check_asset_exists
    from ad_generation_agent.utils import ad_generation_constants
    from adk_common.dtos.errors import ShowableException
    
    if duration_seconds not in [4, 6, 8]:
        return {
            "status": "failed",
            "detail": f"Invalid duration_seconds {duration_seconds} for `generate_video_from_first_frame`. Allowed values are strictly 4, 6, or 8 seconds.",
            "scene_number": scene_number
        }
    
    if reference_images is None:
        reference_images = []
        
    # Resolve the final URLs to ensure fallback logic runs and warms up/checks BQ
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
            "detail": error_msg,
            "scene_number": scene_number
        }

    save_state_property(tool_context, ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL, final_product_uri)
    save_state_property(tool_context, ad_generation_constants.STATE_KEY_PRODUCT_NAME, product_name)
    save_state_property(tool_context, ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL, final_logo_uri)
    save_state_property(tool_context, ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL, main_character_url)
    save_state_property(tool_context, ad_generation_constants.STATE_KEY_ASSET_SHEET_URL, asset_sheet_url)

    # Create a semaphore for this specific generation task to limit concurrency 
    # within this event loop context.
    video_semaphore = asyncio.Semaphore(VIDEO_GENERATION_CONCURRENCY_LIMIT)

    log_message(f"Starting video generation for scene {scene_number}...", Severity.INFO)
    utils_agents.geminienterprise_print(
        tool_context, f"Generating video for scene {scene_number}..."
    )

    try:
        reference_images_list: List[GeneratedMedia] = []
        
        # Priority 1: The starting scene frame MUST come first so it's guaranteed to be used by the video model
        generated_media: GeneratedMedia | None = await utils_agents.load_resource(
            source_path=reference_image, tool_context=tool_context
        )

        if not generated_media or not generated_media.media_bytes:
            message = f"The provided image for scene number `{scene_number}` does not exist or returned empty. The URI provided was: {reference_image}."
            log_message(message, Severity.ERROR)
            response = {
                "status": "failed",
                "detail": message,
                "scene_number": scene_number,
            }
            log_message(f"[generate_video_response] {response}", Severity.ERROR)
            return response
            
        generated_media.description = "Initial Scene Frame. The video must start EXACTLY from this image and maintain continuous consistency."
        reference_images_list.append(generated_media)
        # Enforce Veo 3.1 limitations explicitly: 
        # Only 8-second videos support the `reference_to_video` modality (multiple images).
        # Any other duration strictly requires the `first_frame_to_video` modality (single image).
        if duration_seconds == 8:
            # Priority 2: Asset Sheet
            if asset_sheet_url and asset_sheet_url.strip() and len(reference_images_list) < 3:
                asset_sheet_media = await utils_agents.load_resource(source_path=asset_sheet_url.strip(), tool_context=tool_context)
                if asset_sheet_media and asset_sheet_media.media_bytes:
                    asset_sheet_media.description = "Master Asset Sheet containing the exact visual style, character, and setting required."
                    reference_images_list.append(asset_sheet_media)

            # Priority 3: Main Character Image
            if main_character_url and main_character_url.strip() and len(reference_images_list) < 3:
                char_media = await utils_agents.load_resource(source_path=main_character_url.strip(), tool_context=tool_context)
                if char_media and char_media.media_bytes:
                    char_media.description = "Main Character Reference Image. The generated subject must match this exact identity."
                    reference_images_list.append(char_media)

            # Priority 4: Product Image
            if final_product_uri and len(reference_images_list) < 3:
                product_media = await utils_agents.load_resource(source_path=final_product_uri, tool_context=tool_context)
                if product_media and product_media.media_bytes:
                    product_media.description = "Canonical Product Image. The video must feature this exact product without distortion."
                    reference_images_list.append(product_media)

            # Priority 5: Logo Image
            if final_logo_uri and len(reference_images_list) < 3:
                logo_media = await utils_agents.load_resource(source_path=final_logo_uri, tool_context=tool_context)
                if logo_media and logo_media.media_bytes:
                    logo_media.description = "Brand Logo. Ensure the logo is completely intact and never morphs."
                    reference_images_list.append(logo_media)

        # Prompt step: Enhance the prompt using LLM (Director's Treatment) and technical constraints
        final_prompt = await _enhance_prompt_with_llm(
                prompt, 
                is_logo_scene, 
                tool_context
            )
        
        log_message(f"Calling VEO with prompt: {final_prompt}", Severity.DEBUG)
            
        video_input = VideoGenerationInput(
            raw_prompt=prompt,
            video_query=final_prompt,
            image_identifier=generated_media.filename or "unknown_image",
            duration=duration_seconds,
            is_logo_scene=is_logo_scene,
            scene_number=scene_number,
            reference_images=reference_images_list,
            aspect_ratio=aspect_ratio,
        )

        video_result, error_msg = await generate_single_video(
            video_input=video_input,
            tool_context=tool_context,
            video_semaphore=video_semaphore,
        )


        if video_result:
            # Return response
            response = {
                "status": "success",
                "detail": "Video generated successfully.",
                "scene_number": scene_number,
                "generated_video_uri": str(video_result.get("gcs_uri")),
            }
            
            best_eval = video_result.get("best_eval")
            if best_eval is not None:
                response["evaluation_decision"] = getattr(best_eval, "decision", "Pass")
                response["evaluation_score"] = getattr(best_eval, "averaged_evaluation_score", 10.0)
                response["evaluation_feedback"] = getattr(best_eval, "improvement_prompt", "Looks great.")
                
            response["healing_retry_count"] = healing_retry_count
            if healing_retry_count >= 2 and response.get("evaluation_decision") != "Pass":
                response["evaluation_decision"] = "Forced Pass (Max Retries Exceeded)"
                response["system_warning"] = "MAXIMUM RECOVERY COUNT EXCEEDED. DO NOT RETRY THIS SCENE AGAIN. YOU MUST ACCEPT THIS ASSET AS IS."
                
            log_message(f"[generate_video_response] {response}", Severity.INFO)
            return response
        elif error_msg:
            response = {
                "status": "failed",
                "detail": error_msg,
                "scene_number": scene_number,
            }
            log_message(f"[generate_video_response] {response}", Severity.ERROR)
            return response
        else:
            response = {
                "status": "failed",
                "detail": "Unknown error during video generation.",
                "scene_number": scene_number,
            }
            log_message(f"[generate_video_response] {response}", Severity.ERROR)
            return response

    except Exception as e:
        response = {
            "status": "failed",
            "detail": str(e),
            "scene_number": scene_number,
        }
        log_message(f"[generate_video_response] {response}", Severity.ERROR)
        return response


@log_function_call
async def generate_video_storyboard_batch(
    tool_context: ToolContext,
    storyboard_json: str
) -> str:
    """Generates an entire storyboard of video scenes in parallel based on a single JSON payload.
    
    This unified tool safely unpacks a JSON configuration string to run maximum-latency concurrent video generation.
    It inspects the `generation_modality` field for each scene and routes it to the appropriate backend generator,
    while automatically piping the results through the evaluation algorithms internally.

    Args:
        tool_context (ToolContext): The context for artifact management.
        storyboard_json (str): A valid JSON string containing the global constants and scene-specific variables.
            The JSON string MUST conform to the following schema:
            {
              "product_image_url": "gs://...",
              "product_name": "...",
              "logo_image_url": "gs://...",
              "main_character_url": "gs://...",
              "asset_sheet_url": "gs://...",
              "reference_images": ["gs://..."],
              "scenes": [
                {
                  "scene_number": 1,
                  "prompt": "Description of the scene...",
                  "generation_modality": "first_frame" or "reference_images",
                  "reference_image": "gs://... (required if first_frame)",
                  "is_logo_scene": false,
                  "duration": 4,
                  "healing_retry_count": 1
                }
              ]
            }

    Returns:
        str: A compiled, human-readable markdown scorecard detailing the generated URIs and eval results for every scene.
    """
    
    utils_agents.geminienterprise_print(tool_context, "⚙️ Reading JSON storyboard payload for UNIFIED batch generation...")
    
    try:
        data = json.loads(storyboard_json)
    except Exception as e:
        error_msg = f"Error: Failed to parse storyboard JSON string. Ensure it is perfectly formatted JSON. Details: {e}"
        log_message(error_msg, Severity.ERROR)
        return error_msg

    # Extract Global Configurations
    product_image_url = data.get("product_image_url", "")
    product_name = data.get("product_name", "")
    logo_image_url = data.get("logo_image_url", "")
    main_character_url = data.get("main_character_url", "")
    asset_sheet_url = data.get("asset_sheet_url", "")
    global_reference_images = data.get("reference_images", [])
    
    scenes = data.get("scenes", [])
    if not isinstance(scenes, list):
        return "Error: The `scenes` key inside storyboard_json MUST be a JSON array of objects."

    if not scenes:
        return "Error: No scenes found in the storyboard payload."

    utils_agents.geminienterprise_print(tool_context, "🕒 Pre-flight validating Veo 3.1 duration constraints across all scenes...")
    
    invalid_scenes_errors = []
    
    for scene in scenes:
        scene_num = int(scene.get("scene_number", 1))
        modality = scene.get("generation_modality", "first_frame")
        dur = int(scene.get("duration", scene.get("duration_seconds", 4)))
        
        if modality == "reference_images":
            if dur != 8:
                invalid_scenes_errors.append(f"- Scene {scene_num} requests 'reference_images' but duration is {dur}s. It MUST be exactly 8s.")
        else:
            if dur not in [4, 6, 8]:
                invalid_scenes_errors.append(f"- Scene {scene_num} requests 'first_frame' but duration is {dur}s. It MUST be exactly 4, 6, or 8s.")
                
    if invalid_scenes_errors:
        error_msg = "Error: Batch video generation aborted. The following scenes have invalid durations:\n" + "\n".join(invalid_scenes_errors) + "\nPlease rewrite your JSON payload and retry with correct durations."
        log_message(error_msg, Severity.ERROR)
        return error_msg

    utils_agents.geminienterprise_print(tool_context, f"🚀 Firing off {len(scenes)} concurrent Vertex AI jobs across mixed modalities... This may take 3-10 minutes.")
    
    tasks = []
    
    # Pre-build tasks for concurrent execution
    for scene in scenes:
        scene_number = int(scene.get("scene_number", 1))
        prompt = scene.get("prompt", "")
        generation_modality = scene.get("generation_modality", "first_frame")
        is_logo_scene = bool(scene.get("is_logo_scene", False))
        duration_seconds = int(scene.get("duration", scene.get("duration_seconds", 4)))
        healing_retry_count = int(scene.get("healing_retry_count", 1))
        scene_aspect_ratio = scene.get("aspect_ratio") or data.get("aspect_ratio")
        
        if generation_modality == "reference_images":
            task = generate_video_from_reference_images(
                tool_context=tool_context,
                scene_number=scene_number,
                prompt=prompt,
                duration_seconds=duration_seconds,
                is_logo_scene=is_logo_scene,
                asset_sheet_url=asset_sheet_url,
                logo_image_url=logo_image_url,
                product_image_url=product_image_url,
                main_character_url=main_character_url,
                reference_images=global_reference_images,
                healing_retry_count=healing_retry_count,
                aspect_ratio=scene_aspect_ratio
            )
        else:
            # first_frame modality
            reference_image = scene.get("reference_image", "")
            other_reference_images = scene.get("other_reference_images", [])
            
            task = generate_video_from_first_frame(
                scene_number=scene_number,
                prompt=prompt,
                reference_image=reference_image,
                is_logo_scene=is_logo_scene,
                duration_seconds=duration_seconds,
                tool_context=tool_context,
                product_image_url=product_image_url,
                product_name=product_name,
                logo_image_url=logo_image_url,
                main_character_url=main_character_url,
                asset_sheet_url=asset_sheet_url,
                reference_images=other_reference_images,
                healing_retry_count=healing_retry_count,
                aspect_ratio=scene_aspect_ratio
            )
            
        tasks.append(task)

    # Await all tasks concurrently
    try:
        results = await asyncio.gather(*tasks)
    except Exception as e:
        return f"Error executing parallel asyncio gather for video scenes: {e}"
    
    utils_agents.geminienterprise_print(tool_context, "✅ All rendering tasks complete. Compiling evaluation scorecard...")
    
    # Process results sequentially to build a cohesive string output for the LLM
    final_output = f"### 🎬 Batch Generation & Evaluation Complete ({len(results)} scenes)\n\n"
    
    for idx, res in enumerate(results):
        # We try to get real scene_number from the result payload, else default
        scene_num = res.get("scene_number", idx + 1)
        status = res.get("status", "unknown")
        
        if status == "success":
            uri = res.get("generated_video_uri", "N/A")
            eval_score = res.get("evaluation_score", "No evaluation triggered")
            eval_decision = res.get("evaluation_decision", "Unknown")
            eval_feedback = res.get("evaluation_feedback", "")
            
            icon = "✅" if eval_decision == "Pass" else "⚠️"
            
            final_output += f"#### Scene {scene_num} ({icon} {eval_decision})\n"
            final_output += f"- **Generated URI:** `{uri}`\n"
            final_output += f"- **Score:** {eval_score}/10.0\n"
            final_output += f"- **Critique/Actionable Advice:** {eval_feedback}\n\n"
        else:
            err = res.get("detail", "Error generation")
            final_output += f"#### Scene {scene_num} (❌ Failed to Generate)\n"
            final_output += f"- **Error Detail:** {err}\n\n"

    return final_output


async def _fetch_and_create_ingredient(uri: str, description: str, tool_context: ToolContext) -> Optional[GeneratedMedia]:
    """Helper to fetch a URI from GCS/Artifacts and create the ingredient."""
    if not uri:
        return None
        
    try:
        media = await utils_agents.load_resource(source_path=uri.strip(), tool_context=tool_context)
        if not media or not media.media_bytes:
            return None
            
        media.description = description
        return media
    except Exception as e:
        log_message(f"Failed to fetch ingredient {uri}: {e}", Severity.WARNING)
        return None


@log_function_call
async def generate_video_from_reference_images(
    tool_context: ToolContext,
    scene_number: int,
    prompt: str,
    duration_seconds: int = 8,
    is_logo_scene: bool = False,
    asset_sheet_url: str = "",
    logo_image_url: str = "",
    product_image_url: str = "",
    main_character_url: str = "",
    reference_images: Optional[List[str]] = None,
    healing_retry_count: int = 1,
    aspect_ratio: str | None = None,
) -> Dict[str, Any]:
    """Generates a SINGLE video snippet from a visual description and multiple ingredient reference images.
    
    The engine accepts a maximum of 3 ingredients, resolving explicit parameters in priority order: Asset Sheet, Logo, Product, Character.
    Any remaining slots are filled from the reference_images array.

    Args:
        tool_context: Context for artifact management.
        scene_number: The exact scene number this video belongs to (e.g. 1).
        prompt: The visual story and action for this 4-second snippet. Remember to cite ANY brand style references here!
        duration_seconds: The duration of the video snippet. Usually 4, or 6 for a logo scene.
        is_logo_scene: Whether the corporate logo should prominently end the scene.
        asset_sheet_url: The GCS URI for the master asset sheet (highest priority).
        logo_image_url: The GCS URI for the brand logo (2nd priority).
        product_image_url: The GCS URI for the physical product (3rd priority).
        main_character_url: The GCS URI for the character reference (4th priority).
        reference_images: An array of fallback image URIs to fill the remaining 3 slots.
        healing_retry_count: REQUIRED integer measuring how many times this scene has been retried to heal evaluation failures. Start at 1. Max 2.
        aspect_ratio (str, optional): The target aspect ratio for the generated video (e.g. \"16:9\"). If none, defaults to runtime env.
    """
    from adk_common.dtos.errors import ShowableException
    
    if duration_seconds != 8:
        return {
            "status": "failed",
            "detail": f"Invalid duration_seconds {duration_seconds} for `generate_video_from_reference_images`. The ONLY allowed duration is exactly 8 seconds.",
            "scene_number": scene_number
        }
        
    if reference_images is None:
        reference_images = []
        
    utils_agents.geminienterprise_print(
        tool_context, f"[{scene_number}] Analyzing ingredients and prepping video generation request..."
    )

    try:
        # 1. Resolve images to LabeledReferenceImage, honoring priority order.
        priority_order = [
            ("Logo", logo_image_url, "Brand Logo. Ensure the logo is completely intact and never morphs."),
            ("Product", product_image_url, "Canonical Product Image. The video must feature this exact product without distortion."),
            ("Character", main_character_url, "Main Character Reference Image. The generated subject must match this exact identity."),
            ("Asset Sheet", asset_sheet_url, "Master Asset Sheet containing the exact visual style."),
        ]
        
        final_ingredients = []
        
        for name, uri, desc in priority_order:
            if uri:
                result = await _fetch_and_create_ingredient(uri, desc, tool_context)
                if result:
                    final_ingredients.append(result)
                    
        # 2. Fill the rest of the slots with the general reference_images array
        for uri in reference_images:
            if uri:
                result = await _fetch_and_create_ingredient(uri, "Additional Reference Image.", tool_context)
                if result:
                    final_ingredients.append(result)

        if not final_ingredients:
            log_message("No valid ingredients provided. Falling back to simple video generation.", Severity.WARNING)

        # 3. Create the semaphore
        video_semaphore = asyncio.Semaphore(VIDEO_GENERATION_CONCURRENCY_LIMIT)

        # 4. Enhance the prompt using LLM (Director's Treatment)
        final_prompt = await _enhance_prompt_with_llm(
            prompt, 
            is_logo_scene, 
            tool_context
        )

        # 6. Execute Generation
        video_input = VideoGenerationInput(
            raw_prompt=prompt,
            video_query=final_prompt,
            image_identifier=f"scene_{scene_number}_ingredients",
            duration=duration_seconds,
            is_logo_scene=is_logo_scene,
            scene_number=scene_number,
            reference_images=final_ingredients,
            aspect_ratio=aspect_ratio,
        )

        video_result, error_msg = await generate_single_video_from_ingredients(
            video_input=video_input,
            tool_context=tool_context,
            video_semaphore=video_semaphore,
        )

        if video_result:
            response = {
                "status": "success",
                "detail": "Video generated successfully.",
                "scene_number": scene_number,
                "generated_video_uri": str(video_result.get("gcs_uri")),
            }
            
            best_eval = video_result.get("best_eval")
            if best_eval is not None:
                response["evaluation_decision"] = getattr(best_eval, "decision", "Pass")
                response["evaluation_score"] = getattr(best_eval, "averaged_evaluation_score", 10.0)
                response["evaluation_feedback"] = getattr(best_eval, "improvement_prompt", "Looks great.")
                
            response["healing_retry_count"] = healing_retry_count
            if healing_retry_count >= 2 and response.get("evaluation_decision") != "Pass":
                response["evaluation_decision"] = "Forced Pass (Max Retries Exceeded)"
                response["system_warning"] = "MAXIMUM RECOVERY COUNT EXCEEDED. DO NOT RETRY THIS SCENE AGAIN. YOU MUST ACCEPT THIS ASSET AS IS."
                
            log_message(f"[generate_video_response] {response}", Severity.INFO)
            return response
        elif error_msg:
            response = {
                "status": "failed",
                "detail": error_msg,
                "scene_number": scene_number,
            }
            log_message(f"[generate_video_response] {response}", Severity.ERROR)
            return response
        else:
            response = {
                "status": "failed",
                "detail": "Unknown error during video generation.",
                "scene_number": scene_number,
            }
            log_message(f"[generate_video_response] {response}", Severity.ERROR)
            return response

    except Exception as e:
        response = {
            "status": "failed",
            "detail": str(e),
            "scene_number": scene_number,
        }
        log_message(f"[generate_video_response] {response}", Severity.ERROR)
        return response


import json
import asyncio
from typing import Dict, Any, List

from adk_common.utils.utils_logging import Severity, log_message
from adk_common.utils import utils_agents
from google.adk.tools.tool_context import ToolContext





