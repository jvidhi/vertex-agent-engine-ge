# import asyncio
# import json
# from typing import List, Optional, Tuple

# from ad_generation_agent.utils.scene import Scene
# from adk_common.utils import utils_agents, utils_gcs
# from adk_common.utils.utils_logging import Severity, log_message
# from google.adk.agents.callback_context import CallbackContext
# from google.adk.agents.readonly_context import ReadonlyContext
# from google.adk.tools.tool_context import ToolContext
# from pydantic import BaseModel

# CREATIVE_STATE_KEY = "CREATIVE_STATE"
# CREATIVE_FILENAME = "creative.json"


# class Creative(BaseModel):
#     title: str
#     description: str
#     audio: Optional[str] = None
#     voiceover: Optional[str] = None
#     asset_sheet: Optional[str] = None
#     final_ad_video: Optional[str] = None
#     scenes: List[Scene] = []

#     def get_scene(self, scene_number: int) -> Scene | None:
#         """Retrieves a scene by its number.

#         Args:
#             scene_number: The number of the scene to retrieve.

#         Returns:
#             The Scene object if found, otherwise None.
#         """
#         for scene in self.scenes:
#             if scene.scene_number == scene_number:
#                 return scene
#         return None
    
    
#     def get_or_create_scene(self, scene_number: int) -> Scene:
#         """Retrieves a scene by its number or creates a new one if it doesn't exist.

#         Args:
#             scene_number: The number of the scene to retrieve or create.

#         Returns:
#             The existing or newly created Scene object.
#         """
#         for scene in self.scenes:
#             if scene.scene_number == scene_number:
#                 return scene
#         return Scene(scene_number=scene_number)
    

#     def add_or_update_scene(self, scene: Scene):
#         """Adds a scene or updates it if it already exists.

#         Args:
#             scene: The Scene object to add or update.
#         """
#         updated = False
#         for i, s in enumerate(self.scenes):
#             if s.scene_number == scene.scene_number:
#                 self.scenes[i] = scene
#                 updated = True
#                 break
        
#         if not updated:
#             self.scenes.append(scene)


# def _get_creative(context: ReadonlyContext) -> Optional[Tuple[Creative, bool]]:
#     """Retrieves the Creative object from the context state or GCS.

#     Args:
#         context: The read-only context containing the session state.

#     Returns:
#         A tuple containing the Creative object and a boolean indicating if it was
#         retrieved from GCS (True) or state (False). Returns None if the Creative
#         is not found in either location.
#     """
    
#     # 1. Attempt to retrieve from state
#     data = context.state.get(CREATIVE_STATE_KEY)
#     if isinstance(data, str):
#         try:
#             data = json.loads(data)
#         except json.JSONDecodeError:
#             log_message(f"Failed to decode CREATIVE_STATE from state: {data}", Severity.WARNING)
#             data = None

#     if isinstance(data, dict):
#         return Creative(**data), False
#     if isinstance(data, Creative):
#         return data, False
    
#     # 2. Attempt to retrieve from GCS
#     try:
#         session_id = utils_agents.get_unique_session_id(context)
#         if not session_id:
#             return None
        
#         bucket_name = utils_agents.GOOGLE_CLOUD_BUCKET_ARTIFACTS
#         # Construct URI: gs://bucket/session_id/creative.json
#         # We use normalize_to_gs_bucket_uri to ensure correct format if bucket_name has prefix
#         base_uri = utils_gcs.normalize_to_gs_bucket_uri(bucket_name)
#         uri = f"{base_uri}/{session_id}/{CREATIVE_FILENAME}"
        
#         log_message(f"Attempting to retrieve creative from GCS: {uri}", Severity.DEBUG)
#         content_bytes = utils_gcs.download_bytes_from_gcs(uri)
        
#         if content_bytes:
#             json_str = content_bytes.decode('utf-8')
#             data = json.loads(json_str)
#             creative = Creative(**data)
#             log_message("Retrieved creative from GCS and cached in state.", Severity.INFO)
            
#             return creative, True
            
#     except Exception as e:
#         log_message(f"Failed to retrieve creative from GCS: {e}", Severity.WARNING)

#     return None


# def get_creative(readonly_context: ReadonlyContext) -> Optional[Creative]:
#     """Retrieves the Creative object from the context.

#     This is a wrapper around _get_creative that returns only the Creative object.

#     Args:
#         readonly_context: The read-only context containing the session state.

#     Returns:
#         The Creative object if found, otherwise None.
#     """
#     result = _get_creative(readonly_context)
#     if result:
#         return result[0]
#     else:
#         return None


# def get_or_create_creative(context: CallbackContext) -> Creative:
#     """Retrieves the Creative object or creates a new one if it doesn't exist.

#     If the Creative is retrieved from GCS, it is cached in the session state.
#     If a new Creative is created, it is stored in the session state and uploaded to GCS.

#     Args:
#         context: The callback context containing the session state.

#     Returns:
#         The existing or newly created Creative object.
#     """
#     result = _get_creative(context)
#     if not result:
#         log_message("Creating new creative object.", Severity.INFO)
#         creative = Creative(title="", description="")
#         store_creative(context, creative)
#         return creative
#     else:
#         creative, retrieved_from_gcs = result
#         if retrieved_from_gcs:
#             # Cache in state to avoid repeated GCS fetches
#             log_message("Retrieved creative from GCS, adding to state.", Severity.INFO)
#             context.state[CREATIVE_STATE_KEY] = creative.model_dump_json()
#         return creative
        

# def _upload_creative_to_gcs(bucket_name: str, session_id: str, file_bytes: bytes):
#     """Helper function to run GCS upload in a thread."""
#     try:
#         bucket_path = f"{bucket_name}/{session_id}"
#         utils_gcs.upload_to_gcs(
#             bucket_path=bucket_path,
#             file_bytes=file_bytes,
#             destination_blob_name=CREATIVE_FILENAME
#         )
#         log_message(f"Uploaded {CREATIVE_FILENAME} to GCS.", Severity.DEBUG)
#     except Exception as e:
#         log_message(f"Error uploading {CREATIVE_FILENAME} to GCS: {e}", Severity.ERROR)


# def store_creative(context: CallbackContext, creative: Creative):
#     """Stores the Creative object in the session state and uploads it to GCS.

#     The GCS upload is performed asynchronously to avoid blocking the agent.

#     Args:
#         context: The callback context containing the session state.
#         creative: The Creative object to store.
#     """
#     # 1. Store in Session State (Synchronous)
#     json_creative = creative.model_dump_json()
#     context.state[CREATIVE_STATE_KEY] = json_creative
    
#     log_message(
#         f"Saved creative to state: {json_creative}", Severity.INFO,
#     )

#     # 2. Upload to GCS (Asynchronous)
#     try:
#         session_id = utils_agents.get_or_create_unique_session_id(context)
#         bucket_name = utils_agents.GOOGLE_CLOUD_BUCKET_ARTIFACTS
        
#         json_str = creative.model_dump_json()
#         file_bytes = json_str.encode('utf-8')
        
#         # Schedule the upload in the background
#         try:
#             loop = asyncio.get_running_loop()
#             loop.run_in_executor(
#                 None, 
#                 _upload_creative_to_gcs, 
#                 bucket_name, 
#                 session_id, 
#                 file_bytes
#             )
#         except RuntimeError:
#             # No running loop (e.g., synchronous context), fall back to sync upload or skip
#             log_message("No running event loop. Performing synchronous creative upload.", Severity.ERROR)
#             _upload_creative_to_gcs(bucket_name, session_id, file_bytes)
            
#     except Exception as e:
#         log_message(f"Failed to initiate creative upload: {e}", Severity.ERROR)
