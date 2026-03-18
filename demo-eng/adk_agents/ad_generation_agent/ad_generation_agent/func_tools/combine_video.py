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
"""Combines video clips, audio, and voiceovers into a final video."""

import datetime
import logging
import os
import random
import string
import tempfile
import time
from typing import Dict, List, Optional, Tuple

import moviepy.audio.fx as afx
from ad_generation_agent.utils import ad_generation_constants
from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import (Severity, log_function_call,
                                            log_message)
from google.adk.tools.tool_context import ToolContext
from moviepy import (AudioFileClip, CompositeAudioClip, VideoFileClip,
                     concatenate_videoclips)

BUFFER_SECONDS_UNTIL_END_OF_VIDEO = 3
GOOGLE_CLOUD_BUCKET_ARTIFACTS = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")
RENDER_IMAGES_INLINE = get_required_env_var("RENDER_IMAGES_INLINE").lower() in ("true", "1", "yes")
RENDER_VIDEOS_INLINE = get_required_env_var("RENDER_VIDEOS_INLINE").lower() in ("true", "1", "yes")

# --- Configuration ---


VIDEO_CODEC = "libx264"


# @log_function_call
def _get_storyline_schema(num_images: int) -> List[Dict]:
    """Generates a storyline schema with a dynamic number of scenes.

    Args:
        num_images (int): The total number of images in the storyline.

    Returns:
        A list of dictionaries, where each dictionary defines a scene.
    """
    if num_images <= 0:
        return []

    schema = []
    if num_images > 1:
        schema.append({"name": "before", "generate": True, "step": 0, "duration": 3})

    for i in range(num_images - 2):
        schema.append(
            {
                "name": f"showcase_{i+1}",
                "generate": True,
                "step": i + 1,
                "duration": 3,
            }
        )

    schema.append(
        {
            "name": "logo",
            "generate": True,
            "step": num_images - 1,
            "duration": 5,
        }
    )
    return schema


# @log_function_call
async def _load_single_clip(
    filename: str,
    tool_context: ToolContext,
    temp_dir: str,
    storyline: List[Dict],
) -> Optional[Tuple[VideoFileClip, str]]:
    """Loads and processes a single video clip artifact."""
    try:
        generated_media: GeneratedMedia | None = await utils_agents.load_resource(
            source_path=filename,
            tool_context=tool_context,
        )
        
        if not generated_media or not generated_media.media_bytes:
            log_message(f"Could not load artifact data for {filename}.", Severity.WARNING)
            return None

        temp_path = os.path.join(temp_dir, os.path.basename(filename))
        with open(temp_path, "wb") as f:
            f.write(generated_media.media_bytes)

        clip = VideoFileClip(temp_path)
        clip_index_str = filename.split("_")[0]
        if clip_index_str.isdigit():
            clip_index = int(clip_index_str)
            if 0 <= clip_index < len(storyline):
                duration = storyline[clip_index]["duration"]
                if clip.duration > duration:
                    clip = clip.subclipped(0, duration)
        return clip, temp_path
    except (OSError, ValueError) as e:
        log_message(
            f"Failed to load/process video artifact '{filename}': {e}", Severity.ERROR
        )
        return None


# @log_function_call
async def _load_and_process_video_clips(
    video_files: List[str],
    num_images: int,
    tool_context: ToolContext,
    temp_dir: str,
) -> Tuple[List[VideoFileClip], List[str]]:
    """Loads video artifacts, processes them, and returns clips.

    Args:
        video_files (List[str]): A list of video artifact filenames.
        num_images (int): The number of images in the storyline.
        tool_context (ToolContext): The context for artifact management.
        temp_dir (str): The temporary directory to store video files.

    Returns:
        A tuple containing a list of VideoFileClip objects and their paths.
    """
    video_clips, temp_paths = [], []
    storyline = _get_storyline_schema(num_images)

    for filename in video_files:
        if not filename:
            log_message("Skipping video file with missing filename.", Severity.WARNING)
            continue

        result = await _load_single_clip(filename, tool_context, temp_dir, storyline)
        if result:
            clip, temp_path = result
            video_clips.append(clip)
            temp_paths.append(temp_path)

    return video_clips, temp_paths


# @log_function_call
async def _load_and_process_audio_clips(
    audio_file: str,
    voiceover_file: Optional[str],
    final_duration: float,
    tool_context: ToolContext,
    temp_dir: str,
) -> Optional[CompositeAudioClip]:
    """Loads and processes audio and voiceover files into a composite clip.

    Args:
        audio_file (str): The filename of the background audio artifact.
        voiceover_file (Optional[str]): The filename of the voiceover artifact.
        final_duration (float): The duration of the final video.
        tool_context (ToolContext): The context for artifact management.
        temp_dir (str): The temporary directory to store audio files.

    Returns:
        A CompositeAudioClip or None if no audio is loaded.
    """
    audio_clips = []
    try:
        video_and_sound_length_dif = BUFFER_SECONDS_UNTIL_END_OF_VIDEO
        # Voiceover
        if voiceover_file:           
            generated_media: GeneratedMedia | None = await utils_agents.load_resource(
                source_path=voiceover_file,
                tool_context=tool_context,
            )
            
            if generated_media and generated_media.media_bytes:
                temp_path = os.path.join(temp_dir, os.path.basename(voiceover_file))
                with open(temp_path, "wb") as f:
                    f.write(generated_media.media_bytes)
                vo_clip = AudioFileClip(temp_path)
                
                # Calculate difference between audio and video
                # Split the difference in 2 to ensure audio is "centered"
                video_and_sound_length_dif = max(
                    final_duration, vo_clip.duration
                ) - min(final_duration, vo_clip.duration)
                
                vo_start = max(
                    0,
                    final_duration
                    - vo_clip.duration
                    - video_and_sound_length_dif/2,
                )
                
                audio_clips.append(vo_clip.with_start(vo_start))
                
        # Background audio
        generated_media: GeneratedMedia | None = await utils_agents.load_resource(
            source_path=audio_file,
            tool_context=tool_context,
        )
        
        if generated_media and generated_media.media_bytes:
            temp_path = os.path.join(temp_dir, os.path.basename(audio_file))
            with open(temp_path, "wb") as f:
                f.write(generated_media.media_bytes)
            bg_music_clip = AudioFileClip(temp_path)
            if bg_music_clip.duration < final_duration:
                # Loop the audio if it's shorter than the video
                # TODO: generate new audio if audio is shorter than video
                bg_music_clip = bg_music_clip.with_effects([afx.AudioLoop(duration=final_duration)])
            
            bg_music_clip = (
                bg_music_clip
                .with_duration(final_duration)
                .with_effects([afx.AudioFadeOut(video_and_sound_length_dif/2)])
            )
            audio_clips.append(bg_music_clip)

        if audio_clips:
            return CompositeAudioClip(audio_clips).with_duration(final_duration)
    except (OSError, ValueError) as e:
        log_message(f"Failed to load or process audio: {e}", Severity.ERROR)
    return None


# @log_function_call
async def _combine_and_upload_video(
    video_clips: List[VideoFileClip],
    audio_file: str,
    voiceover_file: Optional[str],
    tool_context: ToolContext,
    temp_dir: str,
) -> Optional[Dict[str, str]]:
    """Combines video clips with audio and uploads the result."""
    try:
        final_clip = concatenate_videoclips(video_clips, method="compose")
        final_clip.audio = await _load_and_process_audio_clips(
            audio_file, voiceover_file, final_clip.duration, tool_context, temp_dir
        )

        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
        random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        filename = f"combined_video_{timestamp_str}_{random_chars}.mp4"
        output_path = os.path.join(temp_dir, filename)
        final_clip.write_videofile(output_path, codec=VIDEO_CODEC)

        with open(output_path, "rb") as f:
            video_bytes = f.read()

        generated_media = GeneratedMedia(
                filename=filename,
                mime_type=ad_generation_constants.VIDEO_MIMETYPE,
                media_bytes=video_bytes,
            )
        
        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            generated_media,
            context=tool_context,
            save_in_gcs=True,
            gcs_folder=utils_agents.get_or_create_unique_session_id(
                tool_context
            ),
            # We already saved it manually above with inline data
            save_in_artifacts=RENDER_VIDEOS_INLINE,
        )

        result = {"name": filename}
        if generated_media.gcs_uri:
            result["gcs_uri"] = generated_media.gcs_uri
            
        return result
    except (OSError, ValueError) as e:
        log_message(f"An error occurred during video combination: {e}", Severity.ERROR)
        return None
    finally:
        for clip in video_clips:
            clip.close()


@log_function_call
async def combine(
    video_files: List[str],
    audio_file: str,
    num_images: int,
    tool_context: ToolContext,
    voiceover_file: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Combines videos, audio, and voiceover into a single file.

    Args:
        video_files (List[str]): A list of video artifact filenames.
        audio_file (str): The filename of the background audio artifact.
        num_images (int): The number of images in the storyline.
        tool_context (ToolContext): The context for tool execution and artifact management.
        voiceover_file (Optional[str]): The filename of the voiceover artifact.
          Defaults to None.

    Returns:
        A dictionary with the combined video artifact name and GCS URI.
    """
    try:
        if not video_files:
            log_message("[combine_response] No video files provided to combine.", Severity.ERROR)
            return None

        with tempfile.TemporaryDirectory() as temp_dir:
            utils_agents.geminienterprise_print(tool_context, "Combining video clips and audio...")
            video_clips, _ = await _load_and_process_video_clips(
                video_files, num_images, tool_context, temp_dir
            )
            if not video_clips:
                log_message("[combine_response] No valid video clips could be loaded.", Severity.ERROR)
                return None

            result = await _combine_and_upload_video(
                video_clips, audio_file, voiceover_file, tool_context, temp_dir
            )
            
            if not result:
                log_message(f"[combine_response] Returning empty result", Severity.ERROR)
            else:
                utils_agents.geminienterprise_print(tool_context, "Final video combined and uploaded.")
                log_message(f"[combine_response] Returning: {result["gcs_uri"]}", Severity.INFO)

            return result
    except Exception as e:
        error_msg = f"Error in combine: {str(e)}"
        log_message(f"[combine_response] {error_msg}", Severity.ERROR)
        return {
            "error": error_msg,
            "system_instruction": "Combining videos failed. Gracefully notify the user."
        }
