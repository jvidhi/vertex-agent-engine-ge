import asyncio
import datetime
import random
import string
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ad_generation_agent.utils import ad_generation_constants
from ad_generation_agent.utils.eval_result import EvalResult
from ad_generation_agent.utils.evaluate_media import evaluate_media
from ad_generation_agent.utils.image_generation import get_gemini_client
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import Severity, log_message
from google import genai
from google.api_core import exceptions as api_exceptions
from adk_common.dtos.errors import ShowableException
from adk_common.media_generation.video_generation import generate_video_bytes, VideoModality
from google.genai.types import (
    GenerateContentConfig,
    GeneratedVideo,
    GenerateVideosConfig,
)
from google.genai.types import Image as GenImage
from google.adk.tools.tool_context import ToolContext

# --- Configuration ---
GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")
VIDEO_GENERATION_MODEL = get_required_env_var("VIDEO_GENERATION_MODEL")
LLM_GEMINI_MODEL_ADGEN_SUBCALLS = get_required_env_var("LLM_GEMINI_MODEL_ADGEN_SUBCALLS") 
VIDEO_DEFAULT_ASPECT_RATIO = get_required_env_var("VIDEO_DEFAULT_ASPECT_RATIO")
VIDEO_GENERATION_EVAL_REATTEMPTS = int(get_required_env_var("VIDEO_GENERATION_EVAL_REATTEMPTS"))
VIDEO_GENERATION_RETRY_DELAY_SECONDS = int(get_required_env_var("VIDEO_GENERATION_RETRY_DELAY_SECONDS"))
VIDEO_GENERATION_STATUS_POLL_SECONDS = int(get_required_env_var("VIDEO_GENERATION_STATUS_POLL_SECONDS"))
VIDEO_GENERATION_TENACITY_ATTEMPTS = int(get_required_env_var("VIDEO_GENERATION_TENACITY_ATTEMPTS"))
VIDEO_FPS = 24
RENDER_VIDEOS_INLINE = get_required_env_var("RENDER_VIDEOS_INLINE").lower() in ("true", "1", "yes")



@dataclass
class VideoGenerationInput:
    """Unified input parameters for video generation with ordered reference images."""
    raw_prompt: str
    video_query: str
    image_identifier: str
    duration: int
    scene_number: int
    is_logo_scene: bool = False
    reference_images: List[GeneratedMedia] = field(default_factory=list)
    aspect_ratio: str | None = None


def _round_to_nearest_veo_duration(duration: int) -> int:
    """Rounds the desired duration to the nearest supported VEO duration."""
    if duration <= 4:
        return 4
    if duration <= 6:
        return 6
    return 8


async def generate_single_video(
    video_input: VideoGenerationInput,
    tool_context: ToolContext,
    video_semaphore: asyncio.Semaphore,
) -> Tuple[Optional[Dict[str, str | int]], Optional[str]]:
    """Generates a single video from a given image and prompt. Evaluates and retries with improvements."""
    best_video: Optional[GeneratedVideo] = None
    error: Optional[str] = None
    best_evaluation: Optional[EvalResult] = None

    video_duration = _round_to_nearest_veo_duration(video_input.duration)
    current_prompt = video_input.video_query
    should_run_eval = VIDEO_GENERATION_EVAL_REATTEMPTS > 0
    vertex_client = get_gemini_client()
    
    for attempt_idx in range(VIDEO_GENERATION_EVAL_REATTEMPTS + 1):
        try:
            log_message(
                f"Generating video attempt {attempt_idx + 1} of {VIDEO_GENERATION_EVAL_REATTEMPTS+1} eval attempts",
                Severity.INFO,
            )
            
            from google.genai import types
            
            eval_tuples = []
            initial_frame_image = None
            
            if video_input.reference_images:
                for idx, labeled_img in enumerate(video_input.reference_images):
                    if not labeled_img.media_bytes:
                        continue
                        
                    # Always pass every reference image to the Gemini Evaluator Judge
                    part = types.Part.from_bytes(data=labeled_img.media_bytes, mime_type=labeled_img.mime_type)
                    eval_tuples.append((part, labeled_img.description))
                    
                    if idx == 0:
                        initial_frame_image = types.Image(image_bytes=labeled_img.media_bytes, mime_type=labeled_img.mime_type)

            utils_agents.geminienterprise_print(
                tool_context,
                f"Generating video clip for {video_input.image_identifier} (Attempt {attempt_idx+1})...",
            )
            
            error = None
            video = None
            
            try:
                # We enforce the semaphore here before yielding to adk_common
                async with video_semaphore:
                    extracted = await generate_video_bytes(
                        client=vertex_client,
                        model=VIDEO_GENERATION_MODEL,
                        prompt=current_prompt,
                        number_of_videos=1,
                        duration_seconds=video_duration,
                        aspect_ratio=video_input.aspect_ratio or VIDEO_DEFAULT_ASPECT_RATIO,
                        person_generation="allow_all",
                        enhance_prompt=True,
                        generate_audio=False,
                        fps=VIDEO_FPS,
                        modality=VideoModality.FIRST_FRAME,
                        initial_frame_image=initial_frame_image,
                        max_retries=VIDEO_GENERATION_TENACITY_ATTEMPTS,
                        retry_delay_min=1.0,
                        retry_delay_max=max(1.0, VIDEO_GENERATION_RETRY_DELAY_SECONDS)
                    )
            
                if extracted:
                    video_bytes, mime_type = extracted[0]
                    from google.genai import types
                    video = types.GeneratedVideo(video=types.Video(video_bytes=video_bytes, mime_type=mime_type))
                else:
                    error = "Extracted videos array was empty."
                    
            except ShowableException as e:
                # Fatal safety failure or unrecoverable error.
                log_message(f"Fatal un-retriable video error: {e.showable_message} | Unmodified Error: {e}", Severity.ERROR)
                error = e.showable_message
                return None, error  # Break the Eval Reattempts loop to send it straight to LLM
            except Exception as e:
                log_message(f"Unexpected video generation API failure | Unmodified Error: {e}", Severity.ERROR)
                error = str(e)

            if error or not (video and video.video and video.video.video_bytes):
                error = error or "Generated video has no content."
                log_message(f"Failed to generate video via API: {error}", Severity.ERROR)
                continue
            else:
                evaluation: EvalResult | None = None
                
                if should_run_eval:
                    evaluation = await evaluate_media(
                        media_bytes=video.video.video_bytes, 
                        mime_type="video/mp4", 
                        evaluation_criteria=video_input.raw_prompt,
                        reference_images=eval_tuples
                    )

                if not best_evaluation:
                    best_evaluation = evaluation
                    best_video = video
                elif evaluation and hasattr(evaluation, 'averaged_evaluation_score') and evaluation.averaged_evaluation_score > best_evaluation.averaged_evaluation_score:
                    best_evaluation = evaluation
                    best_video = video

                if evaluation and evaluation.decision.lower() != "pass":
                    log_message(
                        f"Video did not pass evaluation. Score: {evaluation.averaged_evaluation_score}. Feedback: {evaluation.improvement_prompt}",
                        Severity.WARNING,
                    )
                    
                    # Dynamically patch the Veo prompt for the next iterative try
                    current_prompt = f"{video_input.video_query}\n\nCRITICAL FIXES NEEDED OVER PREVIOUS ATTEMPT: {evaluation.improvement_prompt}"
                    utils_agents.geminienterprise_print(tool_context, f"⚠️ Scene {video_input.scene_number} Evaluation Failed (Score: {evaluation.averaged_evaluation_score}). Retrying internal generation...")
                    continue
                else:
                    best_video = video
                    best_evaluation = evaluation
                    log_message(f"Successfully generated video. Size: {len(video.video.video_bytes)} bytes", Severity.INFO)
                    
                    score_str = f" (Score: {evaluation.averaged_evaluation_score})" if evaluation else ""
                    utils_agents.geminienterprise_print(tool_context, f"✅ Scene {video_input.scene_number} generated successfully{score_str}.")
                    break
        except (api_exceptions.Aborted, ValueError) as e:
            log_message(f"Error in generate_single_video for {video_input.image_identifier}: {e}", Severity.ERROR)
            error = str(e)

    if best_video and best_video.video and best_video.video.video_bytes:
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
        random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        
        filename = f"{ad_generation_constants.SCENE_VIDEO_FILENAME_PREFIX}_{video_input.scene_number}_{timestamp_str}_{random_chars}.mp4"
        generated_media = GeneratedMedia(
            filename=filename,
            mime_type=ad_generation_constants.VIDEO_MIMETYPE,
            media_bytes=best_video.video.video_bytes,
        )

        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            asset=generated_media,
            context=tool_context,
            save_in_gcs=True,
            save_in_artifacts=RENDER_VIDEOS_INLINE,
            gcs_folder=f"{ad_generation_constants.SESSIONS_PREFIX}/{utils_agents.get_or_create_unique_session_id(tool_context)}",
        )

        return_object = {
            "name": filename,
            "duration_seconds": video_duration,
            "scene_description": video_input.video_query,
            "source_image": video_input.image_identifier,
            "scene_number": video_input.scene_number,
            "best_eval": best_evaluation,
        }

        if generated_media and generated_media.gcs_uri:
            return_object["gcs_uri"] = generated_media.gcs_uri

        return return_object, None
    elif error:
        return None, error
    else:
        log_message(f"ERROR: Unknown error while generating video for {video_input.video_query}", Severity.ERROR)
        return None, "Failed to generate video. Unknown error."



async def generate_single_video_from_ingredients(
    video_input: VideoGenerationInput,
    tool_context: ToolContext,
    video_semaphore: asyncio.Semaphore,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Generates a single video from multiple ingredients and prompt. Evaluates and retries with improvements."""
    best_video: Optional[GeneratedVideo] = None
    error: Optional[str] = None
    best_evaluation: Optional[EvalResult] = None

    video_duration = 8  # Veo 3.1 reference_to_video currently only supports exactly 8 seconds.
    current_prompt = video_input.video_query
    should_run_eval = VIDEO_GENERATION_EVAL_REATTEMPTS > 0
    vertex_client = get_gemini_client()
    
    for attempt_idx in range(VIDEO_GENERATION_EVAL_REATTEMPTS + 1):
        try:
            log_message(
                f"Generating video attempt {attempt_idx + 1} of {VIDEO_GENERATION_EVAL_REATTEMPTS+1} eval attempts",
                Severity.INFO,
            )
            
            from google.genai import types
            
            source_payload = {"prompt": current_prompt}
            
            ref_images_api = []
            eval_tuples = []
            if video_input.reference_images:
                for idx, labeled_img in enumerate(video_input.reference_images):
                    if not labeled_img.media_bytes:
                        continue
                        
                    part = types.Part.from_bytes(data=labeled_img.media_bytes, mime_type=labeled_img.mime_type)
                    eval_tuples.append((part, labeled_img.description))
                    
                    if idx < 3:
                        ref_images_api.append(
                            types.VideoGenerationReferenceImage(
                                image=types.Image(image_bytes=labeled_img.media_bytes, mime_type=labeled_img.mime_type),
                                reference_type=types.VideoGenerationReferenceType("asset")
                            )
                        )

            utils_agents.geminienterprise_print(
                tool_context,
                f"Generating video clip for {video_input.image_identifier} using {len(video_input.reference_images) if video_input.reference_images else 0} ingredients (Attempt {attempt_idx+1})...",
            )
            
            error = None
            video = None
            
            try:
                # We enforce the semaphore here before yielding to adk_common
                async with video_semaphore:
                    extracted = await generate_video_bytes(
                        client=vertex_client,
                        model=VIDEO_GENERATION_MODEL,
                        prompt=current_prompt,
                        number_of_videos=1,
                        duration_seconds=video_duration,
                        aspect_ratio=video_input.aspect_ratio or VIDEO_DEFAULT_ASPECT_RATIO,
                        person_generation="allow_all",
                        enhance_prompt=True,
                        generate_audio=False,
                        fps=VIDEO_FPS,
                        modality=VideoModality.REFERENCE_IMAGES,
                        reference_images=ref_images_api,
                        max_retries=VIDEO_GENERATION_TENACITY_ATTEMPTS,
                        retry_delay_min=1.0,
                        retry_delay_max=max(1.0, VIDEO_GENERATION_RETRY_DELAY_SECONDS)
                    )
            
                if extracted:
                    video_bytes, mime_type = extracted[0]
                    from google.genai import types
                    video = types.GeneratedVideo(video=types.Video(video_bytes=video_bytes, mime_type=mime_type))
                else:
                    error = "Extracted videos array was empty."
                    
            except ShowableException as e:
                # Fatal safety failure or unrecoverable error.
                log_message(f"Fatal un-retriable video error: {e.showable_message} | Unmodified Error: {e}", Severity.ERROR)
                error = e.showable_message
                return None, error  # Break the Eval Reattempts loop to send it straight to LLM
            except Exception as e:
                log_message(f"Unexpected video generation API failure | Unmodified Error: {e}", Severity.ERROR)
                error = str(e)

            if error or not (video and video.video and video.video.video_bytes):
                error = error or "Generated video has no content."
                log_message(f"Failed to generate video via API: {error}", Severity.ERROR)
                continue
            else:
                evaluation: EvalResult | None = None
                
                if should_run_eval:
                    evaluation = await evaluate_media(
                        media_bytes=video.video.video_bytes, 
                        mime_type="video/mp4", 
                        evaluation_criteria=video_input.raw_prompt,
                        reference_images=eval_tuples
                    )

                if not best_evaluation:
                    best_evaluation = evaluation
                    best_video = video
                elif evaluation and hasattr(evaluation, 'averaged_evaluation_score') and evaluation.averaged_evaluation_score > best_evaluation.averaged_evaluation_score:
                    best_evaluation = evaluation
                    best_video = video

                if evaluation and evaluation.decision.lower() != "pass":
                    log_message(
                        f"Video did not pass evaluation. Score: {evaluation.averaged_evaluation_score}. Feedback: {evaluation.improvement_prompt}",
                        Severity.WARNING,
                    )
                    
                    # Dynamically patch the Veo prompt for the next iterative try
                    current_prompt = f"{video_input.video_query}\n\nCRITICAL FIXES NEEDED OVER PREVIOUS ATTEMPT: {evaluation.improvement_prompt}"
                    utils_agents.geminienterprise_print(tool_context, f"⚠️ Scene {video_input.scene_number} Evaluation Failed (Score: {evaluation.averaged_evaluation_score}). Retrying internal generation...")
                    continue
                else:
                    best_video = video
                    best_evaluation = evaluation
                    log_message(f"Successfully generated video. Size: {len(video.video.video_bytes)} bytes", Severity.INFO)
                    
                    score_str = f" (Score: {evaluation.averaged_evaluation_score})" if evaluation else ""
                    utils_agents.geminienterprise_print(tool_context, f"✅ Scene {video_input.scene_number} generated successfully{score_str}.")
                    break
        except (api_exceptions.Aborted, ValueError) as e:
            log_message(f"Error in generate_single_video_from_ingredients for {video_input.image_identifier}: {e}", Severity.ERROR)
            error = str(e)

    if best_video and best_video.video and best_video.video.video_bytes:
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
        random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        
        filename = f"{ad_generation_constants.SCENE_VIDEO_FILENAME_PREFIX}_ingredients_{video_input.scene_number}_{timestamp_str}_{random_chars}.mp4"
        generated_media = GeneratedMedia(
            filename=filename,
            mime_type=ad_generation_constants.VIDEO_MIMETYPE,
            media_bytes=best_video.video.video_bytes,
        )

        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            asset=generated_media,
            context=tool_context,
            save_in_gcs=True,
            save_in_artifacts=RENDER_VIDEOS_INLINE,
            gcs_folder=f"{ad_generation_constants.SESSIONS_PREFIX}/{utils_agents.get_or_create_unique_session_id(tool_context)}",
        )

        return_object = {
            "name": filename,
            "duration_seconds": video_duration,
            "scene_description": video_input.video_query,
            "source_image": video_input.image_identifier,
            "scene_number": video_input.scene_number,
            "best_eval": best_evaluation,
        }

        if generated_media and generated_media.gcs_uri:
            return_object["gcs_uri"] = generated_media.gcs_uri

        return return_object, None
    elif error:
        return None, error
    else:
        log_message(f"ERROR: Unknown error while generating video for {video_input.video_query}", Severity.ERROR)
        return None, "Failed to generate video. Unknown error."
