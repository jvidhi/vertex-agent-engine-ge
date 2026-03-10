# ======================================================
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

import json
import os
import random
import time

from typing import Any, Dict, Optional

agent_dir = os.path.dirname(os.path.abspath(__file__))

import mimetypes

from adk_common.dtos.generated_media import GeneratedMedia
from adk_common.utils import utils_agents, utils_gcs, utils_prompts
from adk_common.utils.constants import (get_optional_env_var,
                                        get_required_env_var)
from adk_common.utils.utils_agents import stringify_llm_response
from adk_common.utils.utils_logging import Severity, log_message
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.adk.utils import instructions_utils
from google.genai import types

from .campaign_utils import (Asset, Campaign, Segment, find_campaign_by_name,
                             parse_campaigns_from_xml)

GOOGLE_CLOUD_PROJECT = get_required_env_var("GOOGLE_CLOUD_PROJECT")
LLM_GEMINI_MODEL_MARKETING_ANALYST = get_required_env_var(
    "LLM_GEMINI_MODEL_MARKETING_ANALYST"
)
AGENT_VERSION = get_required_env_var("AGENT_VERSION")
DEMO_COMPANY_NAME = get_optional_env_var("DEMO_COMPANY_NAME", "Vantus")
MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET = get_required_env_var(
    "MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET"
)
SLEEP_SECONDS_GEN_IMAGE = float(get_required_env_var("SLEEP_SECONDS_GEN_IMAGE"))
SLEEP_SECONDS_GEN_VIDEO = float(get_required_env_var("SLEEP_SECONDS_GEN_VIDEO"))
CAMPAIGNS_CONFIG_URL = get_required_env_var("CAMPAIGNS_CONFIG_URL")
RENDER_IMAGES_INLINE = get_required_env_var("RENDER_IMAGES_INLINE").lower() in ("true", "1", "yes")
RENDER_VIDEOS_INLINE = get_required_env_var("RENDER_VIDEOS_INLINE").lower() in ("true", "1", "yes")

AGENT_DESCRIPTION = """
Creative AI assistant specializing in finding insights across the web and enterprise data sources and generating easy-to-consume reports.
Objectives: produce analysis.
"""

SELECTED_CAMPAIGN_FILE_NAME = f"{get_required_env_var("SELECTED_CAMPAIGN_FILE_NAME")}.{AGENT_VERSION}.txt"
SELECTED_ASSET_SHEET_FILE_NAME = f"{get_required_env_var("SELECTED_ASSET_SHEET_FILE_NAME")}.{AGENT_VERSION}.txt"
SESSION_STATE_FILE_NAME = f"{get_required_env_var("SESSION_STATE_FILE_NAME")}.{AGENT_VERSION}.json"

CHOSEN_CAMPAIGN_IDEA_STATE_KEY = "CHOSEN_CAMPAIGN_IDEA"
CHOSEN_ASSET_SHEET_ID_STATE_KEY = "CHOSEN_ASSET_SHEET_ID"

# Global Cache (Singleton) to avoid session state bloat
_CACHED_CAMPAIGNS_LIST: list[Campaign] | None = None
_CACHED_IDEAS_STRING: str | None = None

SELECTED_ASSET_SHEETS_STATE_KEY = "SELECTED_ASSET_SHEETS"
SELECTED_CAMPAIGN_IDEAS_STATE_KEY = "SELECTED_CAMPAIGN_IDEAS"
SELECTED_IMAGES_STATE_KEY = "SELECTED_IMAGES"
SELECTED_VIDEOS_STATE_KEY = "SELECTED_VIDEOS"


def _get_selected_assets_of_type(state_key: str) -> list[str]:
    state_json = _get_state()
    return state_json.get(state_key, [])


def _clear_selected_assets_of_type(state_key: str) -> None:
    state_json = _get_state()
    state_json[state_key] = []
    utils_gcs.upload_to_gcs(
        bucket_path=MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET,
        file_bytes=json.dumps(state_json).encode('utf-8'),
        destination_blob_name=SESSION_STATE_FILE_NAME
    )
    log_message(f"Cleared selected assets of type: {state_key} in GCS. State: {state_json}", Severity.INFO)

def _get_state():
    path = f"gs://{MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET}/{SESSION_STATE_FILE_NAME}"
    try:
        state = utils_gcs.download_text_from_gcs(path)
        state_json = json.loads(state)
        log_message(f"Retrieved session state from GCS: {state_json}", Severity.INFO)
        return state_json
    except Exception as e:
        log_message(f"Failed to retrieve session state from GCS: {e}. Returning empty state.", Severity.WARNING)
        return {}


def _add_selected_asset(state_key: str, asset: str) -> list[str]:
    state_json = _get_state()
    log_message(f"Adding assets of type: {state_key}. Asset: {asset}. All state: {state_json}", Severity.INFO)
    selected_assets = state_json.get(state_key, [])
    if not asset in selected_assets:
        selected_assets.append(asset)
    state_json[state_key] = selected_assets
    utils_gcs.upload_to_gcs(
            bucket_path=MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET,
            file_bytes=json.dumps(state_json).encode('utf-8'),
            destination_blob_name=SESSION_STATE_FILE_NAME
        )
    
    log_message(f"Added assets of type: {state_key}. All state: {state_json}", Severity.INFO)
    return selected_assets


def _get_nonselected_assets(state_key: str, alternatives: list[str]) -> list[str]:
    nonselected_items: list[str] = []
    state_json = _get_state()
    selected_assets = state_json.get(state_key, [])
    for alternative in alternatives:
        if not alternative in selected_assets:
            nonselected_items.append(alternative)
    
    return nonselected_items


def _get_ideas_and_briefs_string(context: ReadonlyContext) -> tuple[str, bool]:
    """
    Retrieves all available marketing campaign ideas and their briefs.
    Returns contents of ideas and briefs config doc & True if retrieved from Cache or False if taken from file.
    """
    global _CACHED_IDEAS_STRING

    # Check global singleton cache first
    if _CACHED_IDEAS_STRING:
        return _CACHED_IDEAS_STRING, True

    # Check session state as fallback (legacy behavior, though we want to move away from it)
    # or just skip it. Let's return from cache if populated.

    try:
        ideas_content = utils_gcs.download_text_from_gcs(CAMPAIGNS_CONFIG_URL)
        if ideas_content:
            _CACHED_IDEAS_STRING = ideas_content
            return ideas_content, False
        log_message(f"Downloaded content from {CAMPAIGNS_CONFIG_URL} was empty.", Severity.WARNING)
    except Exception as e:
        log_message(f"Error reading campaigns config from {CAMPAIGNS_CONFIG_URL}: {e}", Severity.ERROR)

    # Fallback to local file
    local_path = os.path.join(os.path.dirname(__file__), "data_campaigns.xml")
    if os.path.exists(local_path):
        log_message(f"Falling back to local file: {local_path}", Severity.ERROR)
        with open(local_path, "r") as f:
            ideas_content = f.read()
            _CACHED_IDEAS_STRING = ideas_content
            return ideas_content, False
            
    raise RuntimeError("Could not retrieve campaigns config from GCS or local file.")


def _get_and_cache_ideas_and_briefs_object(callback_context: CallbackContext) -> list[Campaign]:
    """Retrieves all available marketing campaign ideas and their briefs."""
    global _CACHED_CAMPAIGNS_LIST

    if _CACHED_CAMPAIGNS_LIST:
        return _CACHED_CAMPAIGNS_LIST

    # Fallback/Initial Load
    xml_string, _ = _get_ideas_and_briefs_string(callback_context)
    campaigns = parse_campaigns_from_xml(xml_string)
    
    if campaigns:
        _CACHED_CAMPAIGNS_LIST = campaigns
        log_message(f"Cached {len(campaigns)} campaigns in global singleton.", Severity.INFO)
    
    return campaigns


def _get_selected_asset_sheet(context: ReadonlyContext) -> Optional[str]:
    selected_asset_sheet = context.state.get(CHOSEN_ASSET_SHEET_ID_STATE_KEY)
    if not selected_asset_sheet:
        try:
            log_message("Selected asset sheet not found in state, retrieving from GCS", Severity.WARNING)
            path = f"gs://{MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET}/{SELECTED_ASSET_SHEET_FILE_NAME}"
            selected_asset_sheet = utils_gcs.download_text_from_gcs(path)
        except Exception as e:
            log_message(f"Failed to retrieve selected asset sheet from GCS: {e}", Severity.WARNING)

    if not selected_asset_sheet:
        log_message("No selected asset sheet found in session state nor GCS.", Severity.WARNING)
        return None
    else:
        log_message(f"Found selected asset sheet: {selected_asset_sheet}", Severity.INFO)
    return selected_asset_sheet


def _get_selected_campaign_name(context: ReadonlyContext) -> Optional[str]:
    selected_campaign_name = context.state.get(CHOSEN_CAMPAIGN_IDEA_STATE_KEY)
    if not selected_campaign_name:
        try:
            log_message("Selected campaign not found in state, retrieving from GCS", Severity.WARNING)
            path = f"gs://{MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET}/{SELECTED_CAMPAIGN_FILE_NAME}"
            selected_campaign_name = utils_gcs.download_text_from_gcs(path)
        except Exception as e:
            log_message(f"Failed to retrieve selected campaign from GCS: {e}", Severity.WARNING)

    if not selected_campaign_name:
        log_message("No selected campaign found in session state nor GCS.", Severity.WARNING)
        return None
    else:
        log_message(f"Found selected campaign name: {selected_campaign_name}", Severity.INFO)
    return selected_campaign_name


def _get_selected_campaign(selected_campaign_name: str, tool_context: ToolContext) -> Optional[Campaign]:
    log_message(f"Retrieving selected campaign with _get_selected_campaign - selected_campaign_name: {selected_campaign_name}", Severity.INFO)
    selected_campaign_name = selected_campaign_name or _get_selected_campaign_name(tool_context) or ""
    
    # log_message(f"Retrieved selected campaign name: {selected_campaign_name}", Severity.INFO)
    campaigns: list[Campaign] = _get_and_cache_ideas_and_briefs_object(tool_context)
    chosen_campaign: Optional[Campaign] = find_campaign_by_name(campaigns, selected_campaign_name)
    
    if not chosen_campaign:
        log_message(f"No campaign found with name: {selected_campaign_name}", Severity.ERROR)

    return chosen_campaign


def get_campaign_idea(tool_context: ToolContext, quantity: int):
    """
    Generates alternative marketing campaign ideas.
    
    Args:
        quantity: Required. The number of campaign ideas to generate. Send 1 unless the user explicitly requests more.
    """

    campaigns = _get_and_cache_ideas_and_briefs_object(tool_context)
    campaign_names = [c.name for c in campaigns]
    
    selected_campaigns = []
    
    quantity = max(quantity, 1)
    for _ in range(quantity):
        valid_alternatives = _get_nonselected_assets(
            SELECTED_CAMPAIGN_IDEAS_STATE_KEY, 
            campaign_names,
        )
        
        if not valid_alternatives:
            # If we run out of fresh ideas during the loop, reset to all
            valid_alternatives = campaign_names

        selected_campaign_name = random.choice(valid_alternatives)
        _add_selected_asset(SELECTED_CAMPAIGN_IDEAS_STATE_KEY, selected_campaign_name)
        log_message(f"Selected campaign name: {selected_campaign_name}", Severity.INFO)

        campaign: Campaign | None = find_campaign_by_name(campaigns=campaigns, name=selected_campaign_name)
        
        if campaign is None:
            log_message(f"Campaign '{selected_campaign_name}' not found, choosing a random one.", Severity.ERROR)
            campaign = random.choice(campaigns)

        selected_campaigns.append({
            "name": campaign.name,
            "hook": campaign.hook,
            "insight": campaign.insight,
            "visual_key": campaign.visual_key,
            "tagline": campaign.tagline,
            "why_it_works": campaign.why_it_works,
            "relevant_brief": campaign.relevant_brief,
            "segments": [s.name for s in campaign.segments]
        })

    _clear_selected_assets_of_type(SELECTED_ASSET_SHEETS_STATE_KEY)
    _clear_selected_assets_of_type(SELECTED_IMAGES_STATE_KEY)
    _clear_selected_assets_of_type(SELECTED_VIDEOS_STATE_KEY)
    log_message(f"Cleared selected assets", Severity.INFO)

    return {
        "campaign_ideas": selected_campaigns
    }


def save_selected_campaign(chosen_idea: str, tool_context: ToolContext):
    """Saves the user's chosen campaign idea to the session state."""

    log_message(f"Entering save_selected_campaign with chosen_idea='{chosen_idea}'", Severity.INFO)

    campaigns: list[Campaign] = _get_and_cache_ideas_and_briefs_object(tool_context)
    chosen_campaign: Optional[Campaign] = find_campaign_by_name(campaigns, chosen_idea)

    if not chosen_campaign:
        campaign_names = [c.name for c in campaigns]
        log_message(f"Could not find selected idea: {chosen_idea}", Severity.ERROR)
        return {
            "status": "error",
            "details": f"There is no campaign with name `{chosen_idea}`. Please check the spelling. Options are: `{'`, `'.join(campaign_names)}`",
        }

    tool_context.state[CHOSEN_CAMPAIGN_IDEA_STATE_KEY] = chosen_campaign.name
    log_message(f"Saving selected idea: {chosen_idea}", Severity.INFO)

    try:
        # session_id = utils_agents.get_or_create_unique_session_id(tool_context)
        utils_gcs.upload_to_gcs(
            bucket_path=MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET,
            file_bytes=chosen_idea.encode('utf-8'),
            destination_blob_name=SELECTED_CAMPAIGN_FILE_NAME
        )
        log_message(f"Uploaded chosen idea to GCS: {SELECTED_CAMPAIGN_FILE_NAME}", Severity.INFO)
    except Exception as e:
        log_message(f"Failed to upload chosen idea to GCS: {e}", Severity.ERROR)

    return {
        "status": "success",
        "details": f"Successfully saved the selected idea: {chosen_campaign.name}",
    }
    

def get_selected_brief(tool_context: ToolContext, selected_campaign_name: str):
    """
    Retrieves the brief for the selected campaign.
    
    Args:
        selected_campaign_name: Required. The name of the selected campaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.
    """
    log_message(f"Entering get_selected_brief with selected_campaign_name='{selected_campaign_name}'", Severity.INFO)
    
    # if selected_campaign_name:
    #     # save_selected_campaign(selected_campaign_name, tool_context)
    #     tool_context.state[CHOSEN_CAMPAIGN_IDEA_STATE_KEY] = selected_campaign_name
    # else:
    #     log_message("Entering get_selected_brief (no campaign name provided)", Severity.ERROR)

    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    return selected_campaign.relevant_brief


async def get_asset_sheet(tool_context: ToolContext, selected_campaign_name: str, quantity: int):
    """
    Generates a new asset sheet configuration for the selected campaign.
    
    Args:
        selected_campaign_name: Required. The name of the selected campaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.
        quantity: Required. The number of asset sheets to generate. Send 1 unless the user explicitly requests more.
    """
    log_message(f"Entering get_asset_sheet with selected_campaign_name='{selected_campaign_name}'", Severity.INFO)
    
    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    if not selected_campaign.asset_sheets:
        log_message("No asset sheets found in session state.", Severity.ERROR)
        return {
            "status": "error",
            "details": f"The chosen campaign (`{selected_campaign.name}`) does not have any asset sheets configured.",
        }
    
    all_alternatives = [s.uri for s in selected_campaign.asset_sheets]
    selected_sheets = []
    
    quantity = max(quantity, 1)
    for _ in range(quantity):
        valid_alternatives = _get_nonselected_assets(
            SELECTED_ASSET_SHEETS_STATE_KEY, 
            all_alternatives,
        )
        
        log_message(f"Valid asset sheet alternatives: {valid_alternatives}", Severity.INFO)
        log_message(f"Used asset sheet alternatives: {_get_selected_assets_of_type(SELECTED_ASSET_SHEETS_STATE_KEY)}", Severity.INFO)
        
        if not valid_alternatives:
            log_message(f"Using all alternative asset sheets as there re no valid asset sheet alternatives", Severity.INFO)
            valid_alternatives = all_alternatives

        selected_sheet_uri = random.choice(valid_alternatives)
        _add_selected_asset(SELECTED_ASSET_SHEETS_STATE_KEY, selected_sheet_uri)
        log_message(f"Selected asset sheet URI: {selected_sheet_uri}", Severity.INFO)
        log_message(f"Used asset sheet alternatives: {_get_selected_assets_of_type(SELECTED_ASSET_SHEETS_STATE_KEY)}", Severity.INFO)
        
        if RENDER_IMAGES_INLINE:
            await _render_asset(selected_sheet_uri, tool_context)
        
        # Add the full asset object to the list
        for sheet in selected_campaign.asset_sheets:
            if sheet.uri == selected_sheet_uri:
                selected_sheets.append(sheet.model_dump())
                break
    
    time.sleep(SLEEP_SECONDS_GEN_IMAGE)

    return {
        "status": "success",
        "asset_sheets": selected_sheets,
    }


def _find_matching_asset_sheet(campaign: Campaign, asset_sheet_uri: str) -> Optional[Asset]:
    """
    Finds an asset sheet in the campaign by matching ID, URI, or Rationale (case-insensitive).
    """
    if not asset_sheet_uri:
        return None
        
    identifier_lower = asset_sheet_uri.lower().strip()
    
    # 1. Exact Match First
    for sheet in campaign.asset_sheets:
        if sheet.id and sheet.id.lower() == identifier_lower:
            return sheet
        if sheet.uri.lower() == identifier_lower:
            return sheet
        if sheet.rationale.lower() == identifier_lower:
            return sheet
            
    # 2. Permissive "Contains" Match (if no exact match)
    # This matches if the identifier is a substring of the rationale or vice versa
    for sheet in campaign.asset_sheets:
        r_lower = sheet.rationale.lower()
        if identifier_lower in r_lower or r_lower in identifier_lower:
            return sheet

    return None


def save_selected_asset_sheet(chosen_sheet_uri: str, tool_context: ToolContext, selected_campaign_name: str):
    """
    Saves the user's chosen asset sheet to the session state.
    
    Args:
        chosen_sheet_uri: The URL of the selected asset sheet.
        selected_campaign_name: Required. The name of the selected campaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.
    """
    
    log_message(f"Entering save_selected_asset_sheet with chosen_sheet_id='{chosen_sheet_uri}', selected_campaign_name='{selected_campaign_name}'", Severity.INFO)

    # if selected_campaign_name:
    #     # save_selected_campaign(selected_campaign_name, tool_context)
    #     tool_context.state[CHOSEN_CAMPAIGN_IDEA_STATE_KEY] = selected_campaign_name

    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    # Use robust lookup
    matched_sheet = _find_matching_asset_sheet(selected_campaign, chosen_sheet_uri)
    
    if matched_sheet and matched_sheet.id:
        # Save the canonical ID
        tool_context.state[CHOSEN_ASSET_SHEET_ID_STATE_KEY] = matched_sheet.uri
        log_message(f"Saving selected asset sheet: {matched_sheet.uri}", Severity.INFO)

        try:
            # session_id = utils_agents.get_or_create_unique_session_id(tool_context)
            utils_gcs.upload_to_gcs(
                bucket_path=MARKETING_ANALYST_DATASTORE_CLOUD_BUCKET,
                file_bytes=matched_sheet.uri.encode('utf-8'),
                destination_blob_name=SELECTED_ASSET_SHEET_FILE_NAME
            )
            log_message(f"Uploaded chosen asset sheet to GCS: {SELECTED_ASSET_SHEET_FILE_NAME}", Severity.INFO)
        except Exception as e:
            log_message(f"Failed to upload asset sheet to GCS: {e}", Severity.ERROR)
        
        log_message(f"Saved selected asset sheet ID: {matched_sheet.id} (matched from '{matched_sheet.uri}')", Severity.INFO)
        return {
            "status": "success",
            "details": f"Successfully saved the selected asset sheet: URL `{matched_sheet.uri}`. ID `{matched_sheet.id}`. (Rationale: {matched_sheet.rationale[:50]}...)",
        }

    # If we get here, no match was found.
    sheet_ids = [s.id for s in selected_campaign.asset_sheets if s.id]
    
    message = f"Could not find asset sheet matching `{chosen_sheet_uri}`."
    log_message(message, Severity.ERROR)
    
    return {
        "status": "error",
        "details": f"{message} Please verify the ID, URL, or Description. Options are: `{'`, `'.join(sheet_ids)}`",
    }


async def _render_asset(asset_url: str, tool_context: ToolContext):
    try:
        gcs_uri = utils_gcs.normalize_to_gs_bucket_uri(asset_url)
        filename = os.path.basename(gcs_uri)
        
        # Determine mime type
        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            if filename.lower().endswith(".mp4"):
                mime_type = "video/mp4"
            else:
                mime_type = "image/png"

        generated_media: GeneratedMedia | None = GeneratedMedia(
            filename=filename,
            mime_type=mime_type,
            gcs_uri=gcs_uri
        )
        
        log_message(f"Rendering asset {asset_url}; filename: {filename}, with mime type: {mime_type}", Severity.DEBUG)
        
        generated_media = await utils_agents.save_to_artifact_and_render_asset(
            asset=generated_media,
            context=tool_context,
            save_in_gcs=False,
            save_in_artifacts=True
        )
    
    except Exception as e:
        log_message(f"Failed to render asset: {e}", Severity.ERROR)
        return {
            "status": "error",
            "details": f"Failed to save and render asset: {e}",
        }

    return {
        "status": "success",
        "details": "asset added to artifacts (and rendered) correctly."
    }
    

async def get_image_ads_for_audience(segment_name: str, tool_context: ToolContext, asset_sheet_uri: str, selected_campaign_name: str, quantity: int):
    """
    Retrieves image ads for a specific audience segment of the selected campaign.
    
    Args:
        segment_name: The name of the segment.
        asset_sheet_uri: Optional. The URI of the asset sheet to filter ads by. If empty, returns all ads for the segment.
        selected_campaign_name: Required. The name of the selected campaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.
        quantity: Required. The number of image ads to generate/retrieve. Send 1 unless the user explicitly requests more.
    """
    log_message(f"Entering get_image_ads_for_audience with segment_name='{segment_name}', asset_sheet_uri='{asset_sheet_uri}', selected_campaign_name='{selected_campaign_name}'", Severity.INFO)

    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    segment: Segment | None = selected_campaign.get_segment_by_name(segment_name)
    if not segment:
        segment_names = [s.name for s in selected_campaign.segments]
        return {
            "status": "error",
            "details": f"The chosen campaign (`{selected_campaign.name}`) has no segment matching `{segment_name}`. Available segments are: `{'`, `'.join(segment_names)}`",
        }
    
    asset_sheet_uri = asset_sheet_uri or _get_selected_asset_sheet(tool_context) or ""
    if not asset_sheet_uri:
        return {
            "status": "error",
            "details": f"Please call again indicating a valid `asset_sheet_uri`. Available asset sheets are: `{'`, `'.join([asset.uri for asset in selected_campaign.asset_sheets])}`",
        }
    
    # Try to resolve the provided ID using robust lookup logic
    matched_sheet = _find_matching_asset_sheet(selected_campaign, asset_sheet_uri)
        
    if matched_sheet:
        save_selected_asset_sheet(matched_sheet.uri, tool_context, selected_campaign_name)
    else:
        return {
            "status": "error",
            "details": f"Please call again indicating a valid `asset_sheet_uri`. The parameter received (`{asset_sheet_uri}`) does not exist. Available asset sheets are: `{'`, `'.join([asset.uri for asset in selected_campaign.asset_sheets])}`",
        }

    # Filter ads by asset sheet ID if provided
    filtered_ads = [
        asset.model_dump() 
        for asset in segment.image_ads 
        if asset.source_asset_sheet_id == matched_sheet.id
    ]

    selected_ads = []
    quantity = max(quantity, 1)
    for _ in range(quantity):
        # Smart Fallback: If no unused ads match the specific sheet, return available ads for the same sheet
        if not filtered_ads or len(filtered_ads) == len (_get_selected_assets_of_type(SELECTED_IMAGES_STATE_KEY)):
            filtered_ads = []
            log_message(f"No unused image ads found for segment '{segment_name}' with sheet '{matched_sheet.id}'. Falling back to ALL asset sheet ads.", Severity.ERROR)
            for segment in selected_campaign.segments:
                for asset in segment.image_ads:
                    if asset.source_asset_sheet_id == matched_sheet.id:
                        filtered_ads.append(asset.model_dump())

        filtered_uris = [str(ad.get("uri")) for ad in filtered_ads]

        valid_alternatives = _get_nonselected_assets(
            SELECTED_IMAGES_STATE_KEY, 
            filtered_uris
        )
        
        log_message(f"Valid image alternatives: {valid_alternatives}", Severity.INFO)
        log_message(f"Used image alternatives: {_get_selected_assets_of_type(SELECTED_IMAGES_STATE_KEY)}", Severity.INFO)
        
        if not valid_alternatives:
            valid_alternatives = filtered_uris

        selected_alternative = random.choice(valid_alternatives)
        _add_selected_asset(SELECTED_IMAGES_STATE_KEY, selected_alternative)
        log_message(f"Selected image: {selected_alternative}", Severity.INFO)
        log_message(f"Used image alternatives: {_get_selected_assets_of_type(SELECTED_IMAGES_STATE_KEY)}", Severity.INFO)
        
        # Find the full asset object
        found = False
        for asset in filtered_ads:
            if str(asset.get("uri")) == selected_alternative:
                if RENDER_IMAGES_INLINE:
                    await _render_asset(selected_alternative, tool_context)
                selected_ads.append(asset)
                found = True
                break
        
        if not found:
            log_message("For an unknown reason the selected asset was not found, returning random", Severity.ERROR)
            random_choice = random.choice(filtered_ads)
            if RENDER_IMAGES_INLINE:
                await _render_asset(random_choice["uri"], tool_context)
            selected_ads.append(random_choice)

    time.sleep(SLEEP_SECONDS_GEN_IMAGE)

    return {
        "status": "success",
        "image_ads": selected_ads,
    }
    

async def get_video_ads_for_audience(segment_name: str, tool_context: ToolContext, asset_sheet_uri: str, selected_campaign_name: str, quantity: int):
    """
    Retrieves video ads for a specific audience segment of the selected campaign.
    
    Args:
        segment_name: The name of the segment.
        asset_sheet_uri: The URI of the asset sheet to filter ads by. If empty, returns all ads for the segment.
        selected_campaign_name: Required. The name of the selected campaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.
        quantity: Required. The number of video ads to generate/retrieve. Send 1 unless the user explicitly requests more.
    """
    log_message(f"Entering get_video_ads_for_audience with segment_name='{segment_name}', asset_sheet_uri='{asset_sheet_uri}', selected_campaign_name='{selected_campaign_name}'", Severity.INFO)

    asset_sheet_uri = asset_sheet_uri or _get_selected_asset_sheet(tool_context) or ""
    
    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    segment: Segment | None = selected_campaign.get_segment_by_name(segment_name)
    if not segment:
        segment_names = [s.name for s in selected_campaign.segments]
        return {
            "status": "error",
            "details": f"The chosen campaign (`{selected_campaign.name}`) has no segment matching `{segment_name}`. Available segments are: `{'`, `'.join(segment_names)}`",
        }

    # Try to resolve the provided ID using robust lookup logic
    matched_sheet = _find_matching_asset_sheet(selected_campaign, asset_sheet_uri)
        
    if matched_sheet:
        save_selected_asset_sheet(matched_sheet.uri, tool_context, selected_campaign_name)
    else:
        return {
            "status": "error",
            "details": f"Please call again indicating a valid `asset_sheet_uri`. The parameter received (`{asset_sheet_uri}`) does not exist. Available asset sheets are: `{'`, `'.join([asset.uri for asset in selected_campaign.asset_sheets])}`",
        }
    
    
    # Filter ads by asset sheet ID if provided
    filtered_ads = [
        asset.model_dump() 
        for asset in segment.video_ads 
        if asset.source_asset_sheet_id == matched_sheet.id
    ]

    selected_ads = []
    quantity = max(quantity, 1)
    for _ in range(quantity):
        # Smart Fallback: If no unusued ads match the specific sheet, return available ads for the same sheet
        if not filtered_ads or len(filtered_ads) == len (_get_selected_assets_of_type(SELECTED_VIDEOS_STATE_KEY)):
            log_message(f"No unused video ads found for segment '{segment_name}' with sheet '{matched_sheet.id}'. Falling back to ALL asset sheet ads.", Severity.ERROR)
            for segment in selected_campaign.segments:
                for asset in segment.video_ads:
                    if asset.source_asset_sheet_id == matched_sheet.id:
                        filtered_ads.append(asset.model_dump())

        filtered_uris = [str(ad.get("uri")) for ad in filtered_ads]

        valid_alternatives = _get_nonselected_assets(
            SELECTED_VIDEOS_STATE_KEY, 
            filtered_uris,
        )
        
        log_message(f"Valid video alternatives: {valid_alternatives}", Severity.INFO)
        log_message(f"Used video alternatives: {_get_selected_assets_of_type(SELECTED_VIDEOS_STATE_KEY)}", Severity.INFO)
        
        if not valid_alternatives:
            valid_alternatives = filtered_uris

        selected_alternative = random.choice(valid_alternatives)
        _add_selected_asset(SELECTED_VIDEOS_STATE_KEY, selected_alternative)
        log_message(f"Selected video: {selected_alternative}", Severity.INFO)
        log_message(f"Used video alternatives: {_get_selected_assets_of_type(SELECTED_VIDEOS_STATE_KEY)}", Severity.INFO)
        
        # Find the full asset object
        found = False
        for asset in filtered_ads:
            if str(asset.get("uri")) == selected_alternative:
                if RENDER_VIDEOS_INLINE:
                    await _render_asset(selected_alternative, tool_context)
                selected_ads.append(asset)
                found = True
                break
        
        if not found:
            log_message("For an unknown reason the selected asset was not found, returning random", Severity.ERROR)
            random_choice = random.choice(filtered_ads)
            if RENDER_VIDEOS_INLINE:
                await _render_asset(random_choice["uri"], tool_context)
            selected_ads.append(random_choice)

    time.sleep(SLEEP_SECONDS_GEN_VIDEO)

    return {
        "status": "success",
        "video_ads": selected_ads,
    }


def recommend_campaign_settings(segment_name: str, tool_context: ToolContext, selected_campaign_name: str):
    """
    Retrieves recommended campaign settings and optimization notes for a specific segment.
    
    Args:
        segment_name: The name of the segment.
        selected_campaign_name: Required. The name of the selected campaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.

    Returns:
        Dict: Contains 'campaign_settings' (Markdown table) and 'optimization_note' (Markdown string).
    """
    log_message(f"Entering recommend_campaign_settings with segment_name='{segment_name}', selected_campaign_name='{selected_campaign_name}'", Severity.INFO)

    # selected_campaign_name = selected_campaign_name or _get_selected_campaign_name(tool_context) or ""
    
    # if selected_campaign_name:
    #     # save_selected_campaign(selected_campaign_name, tool_context)
    #     tool_context.state[CHOSEN_CAMPAIGN_IDEA_STATE_KEY] = selected_campaign_name
    # else:
    #     log_message("Entering recommend_campaign_settings (no campaign name provided)", Severity.ERROR)

    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    segment: Segment | None = selected_campaign.get_segment_by_name(segment_name)
    if not segment:
        segment_names = [s.name for s in selected_campaign.segments]
        return {
            "status": "error",
            "details": f"The chosen campaign (`{selected_campaign.name}`) has no segment matching `{segment_name}`. Available segments are: `{'`, `'.join(segment_names)}`",
        }

    return {
        "status": "success",
        "campaign_settings": segment.campaign_settings,
        "optimization_note": segment.optimization_note,
    }


def get_stocking_projection(tool_context: ToolContext, selected_campaign_name: str):
    """
    Analyzes stocking projections and generates optimization recommendations.

    Args:
        selected_campaign_name: Required. The name of the selectedcampaign. If the agent does not know what value to set, it may call the function with empty string as a rare exception.

    Returns:
        Dict: Contains the 'suggested_optimization' for the campaign.
    """
    log_message(f"Entering get_stocking_projection with selected_campaign_name='{selected_campaign_name}'", Severity.INFO)
    
    selected_campaign: Optional[Campaign] = _get_selected_campaign(selected_campaign_name, tool_context)
    if not selected_campaign:
        log_message("No selected campaign found in session state.", Severity.ERROR)
        return {
            "status": "error",
            # "details": f"It seems the session's state forgot what the selected campaign was. Please call `{save_selected_campaign.__name__}` again to store the chosen campaign or ask the user for confirmation.",
            "details": f"There is no campaign with name: `{selected_campaign_name}`; call again with proper campaign name"
        }
    else:
        save_selected_campaign(selected_campaign_name, tool_context)

    _clear_selected_assets_of_type(SELECTED_CAMPAIGN_IDEAS_STATE_KEY)

    return {
        "status": "success",
        "suggested_optimization": selected_campaign.suggested_optimization,
    }


async def _dynamic_instruction_provider(context: ReadonlyContext) -> str:
    ideas_content, _ = _get_ideas_and_briefs_string(context)
    selected_campaign_name: Optional[str] = _get_selected_campaign_name(context) # context.state.get(CHOSEN_CAMPAIGN_IDEA_STATE_KEY)
    selected_asset_sheet_uri: Optional[str] = _get_selected_asset_sheet(context) # context.state.get(CHOSEN_ASSET_SHEET_ID_STATE_KEY)
    prompt = utils_prompts.load_prompt_file_from_calling_agent(
        {
            "AGENT_VERSION": str(AGENT_VERSION),
            "DEMO_COMPANY_NAME": str(DEMO_COMPANY_NAME),
            "AGENT_NAME": marketing_analyst_agent.name,
            "IDEAS_AND_BRIEFS": ideas_content,
            "SELECTED_CAMPAIGN_NAME": selected_campaign_name if selected_campaign_name else "None",
            "SELECTED_ASSET_SHEET_URI": selected_asset_sheet_uri if selected_asset_sheet_uri else "None",
        }
    )
    return await instructions_utils.inject_session_state(prompt, context)


def _before_agent_callback(callback_context: CallbackContext):
    """Pull and cache files."""
    # _get_and_cache_ideas_and_briefs_object(callback_context)
    log_message(f"BEFORE State: {callback_context.state.to_dict()}",Severity.DEBUG) 
    log_message(f"BEFORE Session ID: {callback_context.session.id}",Severity.DEBUG) 
    log_message(f"BEFORE User ID: {callback_context.session.user_id}",Severity.DEBUG) 
    log_message(f"BEFORE Chosen Campaign: {callback_context.state.get(CHOSEN_CAMPAIGN_IDEA_STATE_KEY)}",Severity.DEBUG) 
    log_message(f"BEFORE Chosen Asset Sheet: {callback_context.state.get(CHOSEN_ASSET_SHEET_ID_STATE_KEY)}",Severity.DEBUG) 
    return None


def _after_agent_callback(callback_context: CallbackContext):
    """Pull and cache files."""
    # _get_and_cache_ideas_and_briefs_object(callback_context)
    log_message(f"AFTER State: {callback_context.state.to_dict()}",Severity.DEBUG) 
    log_message(f"AFTER Session ID: {callback_context.session.id}",Severity.DEBUG) 
    log_message(f"AFTER User ID: {callback_context.session.user_id}",Severity.DEBUG) 
    log_message(f"AFTER Chosen Campaign: {callback_context.state.get(CHOSEN_CAMPAIGN_IDEA_STATE_KEY)}",Severity.DEBUG) 
    log_message(f"AFTER Chosen Asset Sheet: {callback_context.state.get(CHOSEN_ASSET_SHEET_ID_STATE_KEY)}",Severity.DEBUG) 
    return None


def _before_tool_callback(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext) -> dict | None:

    log_message(f"Tool Call: {tool.name}", Severity.INFO)
    log_message(f"Arguments: {args}", Severity.INFO)


marketing_analyst_agent = LlmAgent(
    model=LLM_GEMINI_MODEL_MARKETING_ANALYST,
    name="marketing_analyst_agent",
    description=AGENT_DESCRIPTION,
    instruction=_dynamic_instruction_provider,
    before_agent_callback=_before_agent_callback,
    after_agent_callback=_after_agent_callback,
    before_tool_callback=_before_tool_callback,
    tools=[
        get_campaign_idea,
        save_selected_campaign,
        get_selected_brief,
        get_asset_sheet,
        save_selected_asset_sheet,
        get_image_ads_for_audience,
        get_video_ads_for_audience,
        recommend_campaign_settings,
        get_stocking_projection,
    ],
)


root_agent = marketing_analyst_agent
