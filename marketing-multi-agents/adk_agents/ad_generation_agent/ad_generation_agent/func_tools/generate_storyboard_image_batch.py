import json
import asyncio
from typing import Dict, Any, List

from adk_common.utils.utils_logging import Severity, log_function_call, log_message
from adk_common.utils import utils_agents
from google.adk.tools.tool_context import ToolContext

from ad_generation_agent.func_tools.generate_scene_frame import generate_scene_frame

@log_function_call
async def generate_storyboard_image_batch(
    tool_context: ToolContext,
    storyboard_json: str
) -> str:
    """Generates an entire set of storyboard images in parallel based on a single JSON payload.
    
    This tool safely unpacks a JSON configuration string to run maximum-latency concurrent image generation,
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
              "aspect_ratio": "16:9",
              "scenes": [
                {
                  "scene_number": 1,
                  "prompt": "Description of the scene...",
                  "generation_modality": "first_frame" or "reference_images",
                  "reference_image": "gs://... (the starting image, usually leave empty for new scenes)",
                  "is_logo_scene": false,
                  "other_reference_images": []
                }
              ]
            }

    Returns:
        str: A compiled, human-readable markdown scorecard detailing the generated URIs and evaluation results for every scene image.
    """
    
    utils_agents.geminienterprise_print(tool_context, "⚙️ Reading JSON storyboard payload for batch image generation...")
    
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
    global_aspect_ratio = data.get("aspect_ratio", None)
    
    scenes = data.get("scenes", [])
    if not isinstance(scenes, list):
        return "Error: The `scenes` key inside storyboard_json MUST be a JSON array of objects."

    if not scenes:
        return "Error: No scenes found in the storyboard payload."

    utils_agents.geminienterprise_print(tool_context, f"🚀 Firing off {len(scenes)} concurrent Vertex AI Image generators... This may take 1-3 minutes.")
    
    tasks = []
    
    # Pre-build tasks for concurrent execution
    for scene in scenes:
        scene_number = int(scene.get("scene_number", 1))
        prompt = scene.get("prompt", "")
        generation_modality = scene.get("generation_modality", "first_frame")
        reference_image = scene.get("reference_image", "") # Kept for schema parity, though often empty for new scene images
        is_logo_scene = bool(scene.get("is_logo_scene", False))
        reference_images = scene.get("other_reference_images", [])

        if generation_modality == "reference_images":
            async def _skip_scene(num: int):
                return {
                    "status": "success",
                    "detail": "Skipped static image generation.",
                    "scene_number": num,
                    "generated_image_uri": "Skipped - Not required for 'reference_images' video modality.",
                    "evaluation_decision": "Pass",
                    "evaluation_score": 10.0,
                    "evaluation_feedback": "Skipped natively."
                }
            tasks.append(_skip_scene(scene_number))
            continue

        # If a scene provides a primary reference_image, we add it to the general pool since generate_scene_frame handles it
        all_refs = list(reference_images)
        if reference_image and isinstance(reference_image, str) and reference_image.strip():
            all_refs.append(reference_image.strip())

        scene_aspect_ratio = scene.get("aspect_ratio", global_aspect_ratio)

        # Create the coroutine task object (does not execute until asyncio.gather)
        task = generate_scene_frame(
            scene_number=scene_number,
            prompt=prompt,
            is_logo_scene=is_logo_scene,
            tool_context=tool_context,
            product_image_url=product_image_url,
            product_name=product_name,
            logo_image_url=logo_image_url,
            main_character_url=main_character_url,
            asset_sheet_url=asset_sheet_url,
            reference_images=all_refs,
            aspect_ratio=scene_aspect_ratio
        )
        tasks.append(task)

    # Await all tasks concurrently
    try:
        results = await asyncio.gather(*tasks)
    except Exception as e:
        return f"Error executing parallel asyncio gather for image scenes: {e}"
    
    utils_agents.geminienterprise_print(tool_context, "✅ All rendering tasks complete. Compiling image evaluation scorecard...")
    
    # Process results sequentially to build a cohesive string output for the LLM
    final_output = f"### 🖼️ Batch Image Generation & Evaluation Complete ({len(results)} scenes)\n\n"
    
    for idx, res in enumerate(results):
        # We try to get real scene_number from the result payload, else default
        scene_num = res.get("scene_number", idx + 1)
        status = res.get("status", "unknown")
        
        if status == "success":
            uri = res.get("generated_image_uri", "N/A")
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
