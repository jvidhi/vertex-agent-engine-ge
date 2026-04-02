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
"""Handles the generation of storylines, visual style guides, and asset sheets."""



#KEEPING FOR NOW TO SAVE THE GOOGLE SEARCH LOGIC - WE MIGHT USE IT

import asyncio
import json
import random
import string
import time
from typing import Any, Dict, List, Optional, Tuple, cast

from ad_generation_agent.utils.image_generation import get_gemini_client
from ad_generation_agent.utils.storytelling import STORYTELLING_INSTRUCTIONS
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents, utils_gcs
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import (Severity, log_function_call,
                                            log_message)
from google import genai
from google.adk.tools.tool_context import ToolContext
from google.genai import types

# --- Configuration ---

IMAGE_MIMETYPES = {"image/png", "image/jpeg"}
VIDEO_MIMETYPES = {"video/mp4"}
ALLOWED_MIMETYPES = IMAGE_MIMETYPES | VIDEO_MIMETYPES

# --- Configuration ---
LLM_GEMINI_MODEL_ADGEN_SUBCALLS = get_required_env_var("LLM_GEMINI_MODEL_ADGEN_SUBCALLS")


# @log_function_call
async def generate_storyline(
    target_demographic: str,
    company_name: str,
    prompt: str,
    reference_images: List[str],
    tool_context: ToolContext,
    product_name: str = "",
    number_of_scenes: int = 3,
) -> Dict[str, Any]:
    f"""Generates a storyline and visual style guide for a commercial.

    Args:
        product_name (str): The name of the product to be featured. This is used for product lookup and ad context.
        target_demographic (str): The target audience for the commercial (e.g., "Young families", "Retirees").
        company_name (str): The name of the company (e.g., "Allstate").
        prompt (str): Additional instructions to influence the storyline - either directly provided by the user or inferred to guide the storyline generation.
            * Suggestion: specify the desired visual style (e.g., "Cinematic, warm lighting"), share user's perspective: "the user wants landmarks in the building", note brand guidelines: "the brand is known for its modern, minimalist design, main color should be XXXX, secondary color should be YYYY, etc.".
            * Example: "The style must be cohesive, professional, high-quality, with minimal whitespace and a hyper-realistic effect.".
            * Default to empty string.
        reference_images (List[str]): A list of URIs, URLs or filenames for all the images that should be used as reference for the storyline.
        number_of_scenes (int): The number of scenes/images to generate for the storyboard. Default to 3.

    Returns:
        A dictionary containing the generated content and status.
    """
    try:
        reference_image_parts = []
        
        utils_agents.geminienterprise_print(tool_context, f"Generating storyline for {product_name}...")

        # We map the list of reference images to a dict with empty descriptions as the prompt should contain usage instructions
        # This maintains compatibility with existing logic if needed, but we primarily just need the parts
        for reference_image in reference_images:
            generated_media: GeneratedMedia | None = await utils_agents.load_resource(
                source_path=reference_image, tool_context=tool_context
            )
            
            if generated_media and generated_media.media_bytes:
                part = types.Part.from_bytes(data=generated_media.media_bytes, mime_type=generated_media.mime_type)
                # We append the part directly
                reference_image_parts.append(part)

        story_data = await _generate_storyline_text(
            product=product_name,
            target_demographic=target_demographic,
            company_name=company_name,
            additional_instructions=prompt,
            num_images=number_of_scenes,
            reference_images=reference_image_parts,
        )
        if "error" in story_data:
            response = {"status": "failed", "detail": story_data["error"]}
            log_message(f"[generate_storyline_response] Response: {response}", Severity.ERROR)
            return response

        utils_agents.geminienterprise_print(tool_context, "Storyline text generated.")

        vsg_filename = await _save_json_artifact(
            tool_context,
            "visual_style_guide",
            story_data["visual_style_guide"],
        )
        storyline_filename = await _save_json_artifact(
            tool_context, "storyline", {"storyline": story_data.get("storyline")}
        )
        
        utils_agents.geminienterprise_print(tool_context, "Storyline generation complete.")

        response = {
            "storyline": story_data.get("storyline"),
            "visual_style_guide": story_data.get("visual_style_guide"),
            "brand_guidelines": story_data.get("brand_guidelines"),
            "visual_style_guide_filename": vsg_filename,
            "storyline_filename": storyline_filename,
            "status": "success",
        }
        log_message(f"[generate_storyline_response] Response: {response}", Severity.INFO)
        return response
    except Exception as e:
        log_message(f"Error in generate_storyline: {e}", Severity.ERROR)
        raise e


# @log_function_call
async def _generate_storyline_text(
    product: str,
    target_demographic: str,
    num_images: int,
    additional_instructions: str,
    company_name: str,
    *,
    reference_images: Optional[List[genai.types.Part]] = None,
) -> Dict[str, Any]:
    """Generates the storyline and visual style guide text using a two-phase approach.

    Args:
        product (str): The product to be featured.
        target_demographic (str): The target audience.
        num_images (int): The number of images to generate.
        additional_instructions (str): The visual style description or extra instructions.
        company_name (str): The name of the company to be featured.
        reference_images (Optional[List[genai.types.Part]]): An optional list of reference images.
          Defaults to None.

    Returns:
        A dictionary containing the storyline and visual style guide.
    """
    
    #TODO: activate Google search again later. Disabling for now given it is slow.
    # --- PHASE 1: RESEARCH (Text Mode, Tools Enabled) ---
    # research_summary = "No specific research found. Proceed with general industry knowledge."
    # try:
    #     log_message("Starting Phase 1: Researching brand context...", Severity.INFO)
    #     research_prompt = f"""
    #     Research the brand "{company_name}" and its product "{product}".
        
    #     If the brand exists, summarize its:
    #     1. Brand Voice (e.g., funny, serious, inspiring)
    #     2. Visual Identity (colors, design style)
    #     3. Core Values
        
    #     If the brand appears to be fictional or new, analyze the "{product}" market (e.g., athletic shoes, insurance, coffee) and suggest a plausible, high-quality brand persona that fits current industry trends.
        
    #     Output a concise summary paragraph.
    #     """
    #     research_contents: types.ContentListUnion = [research_prompt]

    #     # We generally don't need reference images for the text research phase, 
    #     # but they are available if we wanted to visually analyze them. 
    #     # For now, keeping it text-focused to be fast and safe.

    #     research_response = await get_gemini_client().aio.models.generate_content(
    #         model=LLM_GEMINI_MODEL_ADGEN_SUBCALLS,
    #         contents=research_contents,
    #         config=types.GenerateContentConfig(
    #             tools=[types.Tool(google_search=types.GoogleSearch())],
    #             response_mime_type="text/plain", 
    #         ),
    #     )
    #     if research_response.text:
    #         research_summary = research_response.text
    #         log_message(f"Phase 1 Research Summary: {research_summary[:200]}...", Severity.INFO)
    #     else:
    #         log_message("Phase 1 yielded empty response.", Severity.WARNING)

    # except Exception as e:
    #     log_message(f"Phase 1 (Research) failed, proceeding with defaults. Error: {e}", Severity.WARNING)


    #Re-add below the following prompt addition to where `--` is above "critical"
    #  ### CONTEXT & RESEARCH
    #     Use the following research summary to inform the brand voice and visual identity:
    #     "{research_summary}"

    # --- PHASE 2: GENERATION (JSON Mode, Tools DISABLED) ---
    try:
        log_message("Starting Phase 2: Generating JSON Storyline...", Severity.INFO)
        
        generation_prompt = f"""
        You are a creative assistant for {company_name}. Your task is to generate a compelling storyline and a detailed visual style guide for a short commercial about the '{product}' for the '{target_demographic}' demographic.
        
        --

        CRITICAL: Each generated scene must take place in a SINGLE, continuous setting. Do not describe multiple locations, time jumps, or cuts within a single scene description. Cuts can only happen between the distinct scenes you are generating.
        
        ### PART 1: VISUAL STYLE (DEFAULT: HYPER-REALISM)
        Unless the user explicitly requests a specific style (e.g., "cartoon", "sketch", "abstract"), you **MUST** design the visual style guide for **Hyper-Realism**.
        *   The imagery should be indistinguishable from high-end photography/videography.
        *   Avoid "dreamy", "surreal", or "AI-generated" aesthetics unless requested.
        *   Focus on realistic lighting, textures, and physics.

        ### PART 2: STORY ARC SELECTION
        You must select the narrative structure that best fits the product type, prioritizing the 'Emerging Story Arc' which is more effective for digital video.
        
        **OPTION A: The Emerging 'Heartbeat' Arc (Default/Preferred)**
        * **Best for:** Lifestyle products, brand awareness, and high-energy engagement.
        * **Structure:** Start High (Strong Hook) -> Unexpected Shifts -> Multiple Peaks -> Resolution.
        * **Requirement:** Do NOT save the product for a big reveal. Integrate subtle brand cues throughout the entire story.

        **OPTION B: The Traditional 'Transformation' Arc**
        * **Best for:** Products that solve a specific problem or offer a visible transformation (e.g., cleaning products, repairs, skincare).
        * **Structure:** Lead-in (The Struggle/Before) -> The Build (The Solution/Purchasing) -> Climax/Reveal (The Result/After).
        * *Constraint:* In the purchasing scene, the character must still have the 'before' look.

        ### PART 3: CREATIVE EXECUTION (ABCD FRAMEWORK)
        Regardless of the arc chosen, your storyline MUST adhere to these specific rules:
        1.  **ATTRACT (Pacing & Framing):** The first scene must be fast-paced. Include at least 2 distinct shots/angles within the opening description to ensure rapid cutting. Specify **TIGHT FRAMING** on subjects (faces/products) for mobile visibility.
        2.  **BRAND (Early Integration):** Introduce the product or brand visually or verbally within the FIRST SCENE (first 5 seconds). If characters are speaking, have them mention the brand name (preferred over voiceover).
        3.  **CONNECT (Emotion):** Combine functional information with an emotional lever (humor, intrigue, or action).
        4.  **DIRECT (Action):** The final scene must include a specific Call-To-Action (e.g., 'Buy Now', 'Visit Site') or a visual Search Bar, and use text cards or animation to emphasize urgency.

        ### PART 4: SCENE GENERATION
        Generate {num_images} scenes based on the selected arc.
        * If generating more than 3 scenes, make the first scene a slow flyover aerial shot of the location without any characters (only if consistent with the 'Start High' requirement).
        * **CRITICAL:** Each generated scene must take place in a SINGLE, continuous setting. Do not describe multiple locations, time jumps, or cuts within a single scene description. Cuts can only happen between the distinct scenes you are generating.
        * **MANDATORY:** Describe each scene as a **SINGLE STATIC SHOT** (or a single continuous camera movement). **DO NOT** describe "alternating shots", "montages", "split screens", or a sequence of different angles in one scene description. The image generation model generates ONE image per scene, so it cannot capture "alternating" views.
        * **MANDATORY:** Unless the user specifies otherwise, the **FINAL** scene MUST be a dedicated 'Logo Scene' where the company logo and product are the primary focus. For this scene, the prompt must emphasize "exact match to reference logo and product".

        Make sure the storyline follows these additional instructions: 
        '{additional_instructions}'

        ### PART 5: REFERENCE IMAGE HANDLING
        You have been provided with reference images. You MUST adhere to the following rules to prevent "conflicting conditioning" in downstream generation:
        1.  **TRUST THE IMAGE:** Do NOT re-describe the physical appearance of characters, products, or locations that are shown in the reference images. The image generation model will use the pixel data as the source of truth.
        2.  **FOCUS ON ACTION:** For these referenced subjects, describe ONLY their actions, emotions, lighting, and camera angles.
        3.  **DO NOT SAY:** "A man with a beard and blue shirt..." (if the reference shows this).
        4.  **DO SAY:** "The SAME man [from reference] runs fast..." or "The product [from reference] sits on the table..."
        5.  **CONFLICT AVOIDANCE:** Textual descriptions that slightly differ from the reference image causes artifacts. Silence is better than description for referenced attributes.

        The visual style guide must describe the necessary imagery. Provide descriptions of characters (with gender and age, adults only), each scene's locations, and a short list of critical props and assets (excluding the {product}). IF A CHARACTER OR ASSET IS COVERED BY A REFERENCE IMAGE, WRITE "Matches Reference Image" INSTEAD OF A PHYSICAL DESCRIPTION.

        {STORYTELLING_INSTRUCTIONS}

        You **MUST** return the output as a single JSON object with three keys: `storyline`, `visual_style_guide`, and `brand_guidelines`.
        * The `storyline` key must contain a single string narrative with {num_images} scenes.
            * Do not refer to other scenes in a scene description; be explicit about what each scene is about.
            * For each Scene, structure it as follows:
                * Scene _: `Title`
                * `Description`
        * The `visual_style_guide` should be itself a json object containing the "characters", "locations", and the "asset_sheet".
        * The "asset_sheet" field in `visual_style_guide` should be a list of strings describing the assets needed for the asset sheet (characters, locations, product).
        * The `brand_guidelines` should be a json object containing "brand_voice", "visual_identity", "core_values", "color_palette", and "typography".
        * **CRITICAL:** The `brand_guidelines` must be detailed and grounded in your research of {company_name}. This information will be used to ensure brand consistency in all subsequent media generation.
        """
        
        contents = []
        contents.append(generation_prompt)
        if reference_images:
            contents.append("\n\nAttached you will find the following images that should be used as reference for the storyline. The prompt contains instructions on how to use them.")
            for part in reference_images:
                contents.append(part)

        # Call with response_mime_type="application/json" and NO tools
        response = await get_gemini_client().aio.models.generate_content(
            model=LLM_GEMINI_MODEL_ADGEN_SUBCALLS,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[], # Explicitly empty tools to prevent tool calling attempts in JSON mode
                response_mime_type="application/json",
                response_json_schema={
                    "type": "object",
                    "properties": {
                        "storyline": {"type": "string"},
                        "brand_guidelines": {
                            "type": "object",
                            "properties": {
                                "brand_voice": {"type": "string"},
                                "visual_identity": {"type": "string"},
                                "core_values": {"type": "string"},
                                "color_palette": {"type": "string"},
                                "typography": {"type": "string"},
                            },
                            "required": ["brand_voice", "visual_identity", "core_values"],
                        },
                        "visual_style_guide": {
                            "type": "object",
                            "properties": {
                                "characters": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                    },
                                },
                                "locations": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "asset_sheet": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["characters", "locations", "asset_sheet"],
                        },
                    },
                    "required": ["storyline", "visual_style_guide", "brand_guidelines"],
                },
            ),
        )
        if response.text:
            try:
                story_data = json.loads(response.text)
                log_message(
                    f"Successfully generated storyline and visual style guide from text (length {len(response.text)})",
                    Severity.INFO,
                )
                return story_data
            except json.JSONDecodeError as e:
                log_message(
                    f"Error decoding JSON response from LLM.: {e}. LLM Response: {response.text}",
                    Severity.ERROR,
                )
                return {"error": f"Error decoding JSON response from LLM: {e}"}
        else:
            return {"error": "Received an empty response from the model."}
    except ValueError as e:
        log_message(f"Error generating storyline text: {e}.", Severity.ERROR)
        return {"error": f"Error generating storyline text: {e}"}


# @log_function_call
async def _save_json_artifact(
    tool_context: ToolContext, name: str, data: Dict[str, Any]
) -> str:
    """Saves a JSON object as a text artifact.

    Args:
        tool_context (ToolContext): The context for saving artifacts.
        name (str): The base name for the artifact file.
        data (Dict[str, Any]): The JSON-serializable data to save.

    Returns:
        The filename of the saved artifact.
    """
    idx = int(time.time() * 1000) % 100
    filename = f"{name}_{idx}.json"
    json_data = json.dumps(data)
    part = genai.types.Part(text=json_data)
    await tool_context.save_artifact(filename, part)
    log_message(f"Saved {name} to artifacts as {filename}", Severity.INFO)
    return filename
