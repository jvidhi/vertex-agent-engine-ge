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
"""Provides prompts for evaluating generated media with a Unified JSON Schema."""

from typing import Optional
from adk_common.utils.utils_logging import log_function_call
from adk_common.utils import utils_prompts

# @log_function_call
def get_image_evaluation_prompt(input_prompt: str, reference_image_descriptions: Optional[list[str]], allow_collage: bool = False) -> str:
    """Generates a detailed prompt for evaluating an AI-generated image.

    Args:
        input_prompt: The original user prompt that was used for image generation.
        reference_image_descriptions: A list of descriptions for each reference image.
        allow_collage: If True, allows collages and storyboards. Defaults to False.

    Returns:
        A formatted string containing the evaluation prompt for the AI model.
    """

    formatted_descriptions = ""
    if reference_image_descriptions:
        formatted_descriptions = "### 1.3. Reference Images\n\nThe user has provided the following reference images:\n"
        formatted_descriptions += "\n".join([f"* {desc}" for desc in reference_image_descriptions]) 

    criteria_6 = """
    6.  **No Storyboard/Collage:** Is the image a single, cohesive scene? It
        should not be a storyboard, collage, split-screen, or contain multiple
        distinct panels."""
    
    if allow_collage:
        criteria_6 = """
    6.  **Collage/Asset Sheet:** If the prompt requests a collage or asset sheet, 
        does the image correctly present multiple distinct elements or panels as requested?
        If the prompt does NOT request a collage, this criterion is N/A (Pass)."""

    variables = {
        "input_prompt": input_prompt,
        "formatted_descriptions": formatted_descriptions,
        "criteria_6": criteria_6
    }
    
    return utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace=variables,
        filename="../prompts/image_evaluation_prompt.md"
    )

# @log_function_call
def get_video_evaluation_prompt(input_prompt: str, reference_image_descriptions: Optional[list[str]]) -> str:
    """Generates a detailed prompt for evaluating an AI-generated video.

    Args:
        input_prompt: The original user prompt that was used for video generation.
        reference_image_descriptions: A list of descriptions for each reference image.

    Returns:
        A formatted string containing the evaluation prompt for the AI model.
    """
    formatted_descriptions = ""
    if reference_image_descriptions:
        formatted_descriptions = "### 1.3. Reference Images\n\nThe user has provided the following reference images:\n"
        formatted_descriptions += "\n".join([f"* {desc}" for desc in reference_image_descriptions]) 

    variables = {
        "input_prompt": input_prompt,
        "formatted_descriptions": formatted_descriptions,
    }
    
    return utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace=variables,
        filename="../prompts/video_evaluation_prompt.md"
    )

# @log_function_call
def get_final_ad_evaluation_prompt(input_prompt: str, reference_image_descriptions: Optional[list[str]]) -> str:
    """Generates a detailed prompt for evaluating a final consolidated video ad.
    
    This prompt is specifically designed for ads with multiple scenes, transitions,
    voiceover, and background music.
    
    Args:
        input_prompt: The original user prompt that was used for video generation.
        reference_image_descriptions: A list of descriptions for each reference image.

    Returns:
        A formatted string containing the evaluation prompt for the AI model.
    """
    formatted_descriptions = ""
    if reference_image_descriptions:
        formatted_descriptions = "### 1.3. Reference Images (CRITICAL)\n\nThe user has provided the following reference images. Compare the LOGO against these:\n"
        formatted_descriptions += "\n".join([f"* {desc}" for desc in reference_image_descriptions]) 

    variables = {
        "input_prompt": input_prompt,
        "formatted_descriptions": formatted_descriptions,
    }
    
    return utils_prompts.load_prompt_file_from_calling_agent(
        variables_to_replace=variables,
        filename="../prompts/final_ad_evaluation_prompt.md"
    )