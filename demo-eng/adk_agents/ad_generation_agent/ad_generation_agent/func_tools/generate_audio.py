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
"""Generates background audio and voiceovers using Google Cloud services."""

import asyncio
import base64
import random
import datetime
import random
import string
import time
from typing import Any, Dict, Optional, cast

import aiohttp
import google.auth
import google.auth.transport.requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential
from ad_generation_agent.utils import ad_generation_constants
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.constants import get_required_env_var, get_optional_env_var
from adk_common.utils.utils_logging import (Severity, log_function_call,
                                            log_message)
from google.adk.tools.tool_context import ToolContext
from google.api_core import exceptions as api_exceptions
from google.genai import types

# --- Configuration ---

AUDIO_GENERATION_TENACITY_ATTEMPTS = int(get_optional_env_var("AUDIO_GENERATION_TENACITY_ATTEMPTS", "3"))
STATIC_AUDIO_FALLBACK = "static/audio/audio_track_1.mp3"
AUDIO_TTS_GENERATION_MODEL = get_optional_env_var("AUDIO_TTS_GENERATION_MODEL", "")
AUDIO_TTS_VOICE_NAME = get_optional_env_var("AUDIO_TTS_VOICE_NAME", "")
AUDIO_LYRIA_GENERATION_MODEL = get_required_env_var("AUDIO_LYRIA_GENERATION_MODEL")
AUDIO_GENERATION_TENACITY_ATTEMPTS = int(get_required_env_var("AUDIO_GENERATION_TENACITY_ATTEMPTS"))


ALL_VOICES = {
"Achernar": "Female. Soft, clear mid-range voice with a friendly and engaging tone. Best for explainers, podcast intros, and content requiring a gentle touch.",
"Achird": "Male. Youthful, inquisitive, and slightly breathy voice. Ideal for tutorials, educational content for younger audiences, or casual explainers.",
"Algenib": "Male. Gravelly and textured. Use for dramatic storytelling, character voices in games, or content requiring a rugged, serious tone.",
"Algieba": "Male. Smooth and polished. Suitable for general narration, corporate presentations, and content that needs a seamless, professional delivery.",
"Alnilam": "Male. Firm and steady. Best for news broadcasting, official announcements, and delivering factual information with authority.",
"Aoede": "Female. Breezy, clear, and conversational. Excellent for podcasts, e-learning, and 'elated' or congratulatory messages where a thoughtful yet lighthearted tone is needed.",
"Autonoe": "Female. Bright, mature, and resonant with a deeper tone. Perfect for documentaries, audiobooks, and serious narration requiring a calm presence.",
"Callirrhoe": "Female. Easy-going, confident, and articulate professional voice. The 'gold standard' for business narration, customer support agents, and casual yet polite conversation.",
"Charon": "Male. Deep, authoritative, and informative. Best suited for news reading, serious broadcasts, documentaries, and content requiring gravity and trust.",
"Despina": "Female. Warm, inviting, and trustworthy. Ideal for commercials, customer service interactions, and welcoming messages.",
"Enceladus": "Male. Breathy and textured. Often used for specific character emotions (like sounding tired or bored) or atmospheric storytelling.",
"Erinome": "Female. Professional, articulate, and clear. Best for education, museum guides, and formal instruction where clarity is paramount.",
"Fenrir": "Male. Excitable, deep, and resonant. Great for passionate storytelling, poetry recitation, or high-energy narratives that need 'majestic' qualities.",
"Gacrux": "Female. Mature, smooth, and authoritative yet approachable. Excellent for corporate videos, high-level instruction, and professional training materials.",
"Iapetus": "Male. Clear and neutral. A versatile choice for general purpose text-to-speech where the content should take center stage over the voice personality.",
"Kore": "Female. Firm, energetic, and professional with a slightly higher pitch. Best for upbeat advertisements, tutorials, and standard announcements.",
"Laomedeia": "Female. Upbeat, inquisitive, and conversational. Great for explainers, FAQs, and content that aims to engage the listener actively.",
"Leda": "Female. Youthful, clear, and composed. Suitable for professional narration, e-learning, and content targeting a modern audience.",
"Orus": "Male. Firm and direct. Use for serious announcements, warnings, or instructional content that requires strict adherence.",
"Puck": "Male. Upbeat, conversational, and friendly. Perfect for fun content, character voices, excited narration, or casual apps.",
"Pulcherrima": "Female. Forward, bright, and energetic. Best for commercials, promotional videos, and character voices that need to cut through noise.",
"Rasalgethi": "Male. Informative and neutral. Ideal for news briefings, weather reports, and factual data delivery.",
"Sadachbia": "Male. Lively and dynamic. Use for advertisements, energetic promos, and content that needs to keep the listener moving.",
"Sadaltager": "Male. Knowledgeable and steady. Best for educational videos, expert narration, and technical explainers.",
"Schedar": "Male. Even and consistent. Suitable for long-form reading, lists, and content where a flat, non-distracting delivery is preferred.",
"Sulafat": "Female. Warm, confident, and persuasive. Excellent for marketing narration, sales pitches, and content intended to convince or reassure.",
"Umbriel": "Male. Easy-going and relaxed. Best for casual narration, lifestyle content, and blogs.",
"Vindemiatrix": "Female. Gentle, calm, and thoughtful with a lower pitch. Perfect for meditation guides, wellness apps, and reflective content.",
"Zephyr": "Female. Bright, perky, and energetic with a higher pitch. Ideal for children's content, high-energy commercials, and friendly notifications.",
"Zubenelgenubi": "Male. Casual and informal. Use for social media content, vlogs, and scenarios requiring a 'friend-next-door' vibe."
}


# @log_function_call
# @retry(
#     stop=stop_after_attempt(AUDIO_GENERATION_TENACITY_ATTEMPTS),
#     wait=wait_random_exponential(multiplier=2, min=1, max=33),
#     retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
# )
async def _send_google_api_request(
    api_endpoint: str, data: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Sends an authenticated HTTP request to a Google API endpoint.

    Args:
        api_endpoint (str): The URL of the Google API endpoint.
        data (Optional[Dict[str, Any]]): A dictionary of data to send in the request body.
          Defaults to None.

    Returns:
        The JSON response from the API, or None on failure.
    """
    exception: Exception | None = None
    for _ in range(AUDIO_GENERATION_TENACITY_ATTEMPTS):
        try:
            creds, _ = google.auth.default()
            auth_req = google.auth.transport.requests.Request()
            creds.refresh(auth_req) # type: ignore
            access_token = creds.token # type: ignore

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_endpoint, headers=headers, json=data
                ) as response:
                    response.raise_for_status()
                    return await response.json()

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log_message(f"Error calling {api_endpoint}: {e}. Data: {data}", Severity.WARNING)
            exception = e
            continue
        except Exception as e:
            log_message(f"Unexpected error calling {api_endpoint}: {e}. Type: {type(e)}. Data: {data}", Severity.ERROR)
            # Don't retry unexpected errors unless we verify they are transient
            return None
    
    if exception:
        raise exception
    
    log_message(f"Failed to call {api_endpoint} after {AUDIO_GENERATION_TENACITY_ATTEMPTS} attempts", Severity.ERROR)
    return None


# @log_function_call
async def _generate_audio(
    audio_query: str, tool_context: ToolContext
) -> dict[str, str | GeneratedMedia]:
    """Generates an audio clip using the Lyria model.

    Args:
        audio_query (str): The prompt describing the desired audio content.
        tool_context (ToolContext): The context for artifact management.

    Returns:
        A dictionary with the generated audio artifact name, or a fallback.
    """
    project_id = get_required_env_var("GOOGLE_CLOUD_PROJECT")

    endpoint = (
        f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project_id}"
        f"/locations/us-central1/publishers/google/models/{AUDIO_LYRIA_GENERATION_MODEL}:predict"
    )
    payload = {"instances": [{"prompt": audio_query}], "parameters": {"sampleCount": 1}}

    try:
        response = await _send_google_api_request(endpoint, payload)
        if not response or "predictions" not in response:
            raise ValueError(f"Invalid response from Lyria model. response: {response}. predictions: {response.get('predictions') if response else None}")

        prediction = response["predictions"][0]
        bytes_b64 = prediction.get("audioContent") or prediction.get("bytesBase64Encoded")
        if not bytes_b64:
            raise ValueError("No audio data in prediction.")

        audio_data = base64.b64decode(bytes_b64)
        
        # Microsecond Timestamp + Random Chars
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
        random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        filename = f"audio_{timestamp_str}_{random_chars}.wav"
        
        generated_media: GeneratedMedia | None = GeneratedMedia(
            filename=filename,
            mime_type=ad_generation_constants.AUDIO_MIMETYPE,
            media_bytes=audio_data,
        )
        
        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            asset=generated_media,
            context=tool_context,
            save_in_gcs=True,
            gcs_folder=f"{ad_generation_constants.SESSIONS_PREFIX}/{utils_agents.get_or_create_unique_session_id(tool_context)}",
        )
        
        return {
            "audio_type": "generated_audio",
            "media": generated_media
        }
    except (aiohttp.ClientError, ValueError) as e:
        log_message(f"Error generating audio: {e}. Type: {type(e)}", Severity.ERROR)
        log_message(f"_generate_audio failed with exception: `{e}` but fallback logic is not implemented yet", Severity.ERROR)
        #TODO: implement fallback logic - return static audio
        raise e


# @log_function_call
async def _generate_voiceover(
    prompt: str,
    text: str,
    tool_context: ToolContext,
    voice_name: str,
) -> dict[str, Any]:
    """Generates a voiceover using Gemini's multimodal capabilities."""
    
    log_message(f"Generating voiceover with Gemini. Text: {text}", Severity.INFO)
    
    from ad_generation_agent.utils.image_generation import get_gemini_client
    client = get_gemini_client()

    # Combine styling prompt and the actual text
    full_prompt = f"Styling instructions: {prompt}\n\nText to speak: {text}"
    
    try:
        response = await client.aio.models.generate_content(
            model=AUDIO_TTS_GENERATION_MODEL,
            contents=[full_prompt],
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
            )
        )
        
        audio_content = None
        if (
            response
            and response.candidates
            and response.candidates[0]
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                    audio_content = part.inline_data.data
                    break
        
        if not audio_content:
            raise RuntimeError("Gemini API returned no audio content for voiceover.")

        # Microsecond Timestamp + Random Chars
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
        random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        filename = f"voiceover_{timestamp_str}_{random_chars}.mp3"
        
        generated_media: GeneratedMedia | None = GeneratedMedia(
            filename=filename,
            mime_type=ad_generation_constants.AUDIO_MIMETYPE,
            media_bytes=audio_content,
        )
        
        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            asset=generated_media,
            context=tool_context,
            save_in_gcs=True,
            gcs_folder=f"{ad_generation_constants.SESSIONS_PREFIX}/{utils_agents.get_or_create_unique_session_id(tool_context)}",
        )
        
        return {
            "audio_type": "generated_voiceover",
            "media": generated_media
        }

    except Exception as e:
        log_message(f"Failed to generate voiceover with Gemini: {e}", Severity.ERROR)
        raise e


@log_function_call
async def generate_audio_and_voiceover(
    tool_context: ToolContext,
    audio_query: str,
    voiceover_prompt: str,
    voiceover_text: str,
    voiceover_voice: str = "Aoede",
    generation_mode: str = "both",
) -> Dict[str, Any]:
    """
    Generates a background audio track, a voiceover (using Gemini), or both in a single function call.
    This function can run generation processes concurrently for improved performance when generating both.

    Args:
        audio_query (str): The prompt describing the desired background audio content.
        voiceover_prompt (str): The prompt that sets the context for the voiceover. e.g. You are a professional announcer with a warm, friendly tone.
        voiceover_text (str, optional): Explicit text for the voiceover to sell the product. Make it punny and mention the company name. Keep it short and sweet. e.g. FALL into great prices from {company name} - buy from a store near you!
                                        IMPORTANT: You MUST calculate the total video duration (number of scenes * duration per scene) and ensure the `voiceover_text` fits as exactly as possible but is never longer than the video.
                                        *   Rule of thumb: ~1 words per second.
                                        *   Err on the side of shorter voiceovers to avoid abrupt ending or truncation.
        voiceover_voice (str, optional): The specific voice to use for the voiceover (Note: Gemini TTS might use its own voice selection based on the prompt).
        generation_mode (str, optional): Specifies what to generate. Can be 'audio', 'voiceover', or 'both'.
                                         Defaults to 'both'.
        
    
    Returns:
        Optional[dict]: A dictionary containing the names of the generated audio and voiceover artifacts,
                        and a list of any failures, or None if the operation fails completely.
    """
    try:    
        tasks = []
        if generation_mode in ["audio", "both"]:
            tasks.append(_generate_audio(audio_query, 
                                         tool_context))
        if generation_mode in ["voiceover", "both"]:
            tasks.append(
                _generate_voiceover(
                    prompt=voiceover_prompt,
                    text=voiceover_text,
                    tool_context=tool_context,
                    voice_name=voiceover_voice
                )
            )
            
        utils_agents.geminienterprise_print(tool_context, "Generating audio and voiceover...")

        if not tasks:
            log_message(f"Invalid generation_mode: {generation_mode}", Severity.ERROR)
            response = {"failures": [f"Invalid generation_mode: {generation_mode}. You must specify either 'audio', 'voiceover', or 'both'."]}
            log_message(f"[generate_audio_and_voiceover_response] Response: {response}", Severity.ERROR)
            return response

        results = await asyncio.gather(*tasks, return_exceptions=True)
        response: Dict[str, Any] = {"failures": []}
        for result in results:
            if isinstance(result, Exception):
                response["failures"].append(f"{result}")
            elif isinstance(result, dict) and "media" in result and isinstance(result["media"], GeneratedMedia):
                media: GeneratedMedia = cast(GeneratedMedia, result["media"])
                response[result["audio_type"]] = media.gcs_uri
            else:
                log_message(f"Unknown response while generating audio & voiceover. Object received: {result}", Severity.ERROR)
                response["failures"].append(f"Unknown error generating media")

        utils_agents.geminienterprise_print(tool_context, "Voiceover & audio generated.")
        log_message(f"[generate_audio_and_voiceover_response] Response: {response}", Severity.INFO)
        return response
    except Exception as e:
        error_msg = f"Error in generate_audio_and_voiceover: {str(e)}"
        log_message(error_msg, Severity.ERROR)
        return {
            "failures": [error_msg],
            "system_instruction": "Audio/Voiceover generation failed. Do NOT crash. Tell the user what happened."
        }
