import asyncio
import os
import sys

# Setup environment exactly as the agent does
from adk_common.utils.env_loader import load_env_cascade
load_env_cascade("/Users/gvarelal/Documents/demos/cde-github/demo-eng-dev/adk_agents/ad_generation_agent/ad_generation_agent/agent.py")

from google.adk.tools.tool_context import ToolContext
from ad_generation_agent.func_tools.generate_video import generate_video_from_first_frame

async def main():
    print("Testing generate_video backend directly to verify eval loops...")
    
    # Needs vertexai init since we skipped agent.py
    import vertexai
    vertexai.init(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"), 
        location=os.environ.get("GOOGLE_CLOUD_LOCATION")
    )
    from unittest.mock import MagicMock, AsyncMock
    mock_ctx = MagicMock()
    mock_ctx.session.state = {}
    mock_ctx.artifact_service = MagicMock()
    mock_ctx.artifact_service.get_artifact = AsyncMock(return_value=None)
    mock_ctx.artifact_service.load_artifact = AsyncMock(return_value=None)
    mock_ctx.artifact_service.save_artifact_async = AsyncMock(return_value=None)
    mock_ctx.artifact_service.create_artifact = AsyncMock(return_value=None)
    import adk_common.utils.utils_agents as u_agents
    
    async def mock_save_to_artifact(asset, *args, **kwargs):
        media_bytes = asset.media_bytes
        media_name = asset.filename
        save_path = f"/Users/gvarelal/.gemini/jetski/brain/8836c325-83e8-4446-ba81-ab2c378d26b0/final_{media_name}"
        if media_bytes:
            with open(save_path, "wb") as f:
                f.write(media_bytes)
            print(f"!!! SAVED VIDEO TO: {save_path} !!!")
        else:
            print("!!! NO VIDEO BYTES TO SAVE !!!")
            
        asset.gcs_uri = f"gs://mock/{media_name}"
        return asset

    u_agents.save_to_artifact_and_render_asset = mock_save_to_artifact
    
    tool_context = ToolContext(invocation_context=mock_ctx)
    
    print("Initiating video generation with aggressive auto-evaluation...")
    try:
        res = await generate_video_from_first_frame(
            scene_number=1,
            prompt="A pair of sleek, high-performance athletic shoes resting on a rugged mountain trail at sunrise. Photorealistic, cinematic lighting, exactly matching the attached shoes in color and style.",
            reference_image="https://storage.googleapis.com/nrf-marketing-assets/catalyst_shoes.png",
            is_logo_scene=False,
            duration_seconds=4,
            tool_context=tool_context
        )
        print("============ FINAL GENERATION RESULT ============")
        print(res.get("status"), res.get("detail"))
        
        if res.get("video_bytes"):
            from ad_generation_agent.utils.evaluate_media import evaluate_media
            print("============ INDEPENDENT EVALUATION ============")
            eval_res = await evaluate_media(
                media_bytes=res["video_bytes"],
                mime_type="video/mp4",
                evaluation_criteria="Extremely high quality athletic shoe commercial. Rugged trail. No morphing. Perfect lighting.",
                is_final_ad=True
            )
            print("Decision:", eval_res.decision)
            print("Averaged Score:", eval_res.averaged_evaluation_score)
            print("Feedback:", eval_res.improvement_prompt)
        else:
            print("No video_bytes found in result.")
    except Exception as e:
        print(f"Error executing generate_video: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
