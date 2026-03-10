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
import difflib
import tomllib
import traceback
from typing import Optional, Dict, Any
from google.adk.tools.tool_context import ToolContext
from adk_common.utils.constants import get_required_env_var
from adk_common.utils.utils_logging import log_message, Severity
from adk_common.utils import utils_gcs
from adk_common.dtos.agent_tool_response import AgentToolResponse, Status
from ad_generation_agent.utils import ad_generation_constants

def retrieve_brand_identity(company_name: str, tool_context: ToolContext) -> dict[str, Any]:
    """Retrieves brand guidelines, visual style, and exact asset URLs from a structured catalog configuration.
    
    Args:
        company_name: The name of the brand or company requested by the user.
    """
    try:
        bucket_name = get_required_env_var("GOOGLE_CLOUD_BUCKET_BRAND_CONFIGS")

        # We look for toml configs under a specific prefix in the bucket
        prefix = "brand_configs/"
        toml_texts = utils_gcs.get_text_files_from_gcs_bucket(
            bucket_name=bucket_name,
            prefix=prefix,
            allowed_extensions=[".toml"]
        )
        
        brand_configs = []
        for text_content in toml_texts:
            try:
                config_data = tomllib.loads(text_content)
                if "brand_name" in config_data:
                    brand_configs.append(config_data)
            except Exception as parse_e:
                log_message(f"Error parsing TOML from GCS: {parse_e}", Severity.WARNING)

        if not brand_configs:
            msg = "No brand configurations found in the catalog."
            log_message(msg, Severity.WARNING)
            return AgentToolResponse(
                status=Status.SUCCESS, 
                detail=msg + " You must generate all context from scratch based on the user's prompt without specific asset URLs."
            ).convert_to_agent_response()

        # Extract available brand names for fuzzy matching
        available_brands = [config["brand_name"] for config in brand_configs]
        
        log_message(f"Searching for '{company_name}' among: {available_brands}", Severity.INFO)
        
        # Use difflib for fuzzy matching with a 60% similarity threshold
        matches = difflib.get_close_matches(
            company_name.lower(),
            [b.lower() for b in available_brands],
            n=1,
            cutoff=0.6
        )

        if matches:
            matched_name_lower = matches[0]
            # Find the original config dictionary
            matched_config = next(c for c in brand_configs if c["brand_name"].lower() == matched_name_lower)
            
            log_message(f"Match found: {matched_config['brand_name']}", Severity.INFO)
            
            # Save the payload into state for prompt injection on the next turn
            tool_context.state[ad_generation_constants.STATE_KEY_BRAND_CONTEXT_PAYLOAD] = matched_config
            
            from adk_common.utils.utils_state import save_state_property
            from adk_common.utils.utils_agents import check_asset_exists

            urls_to_validate = {
                ad_generation_constants.STATE_KEY_PRODUCT_IMAGE_URL: matched_config.get("hero_product_image_reference") or matched_config.get("product_image_url"),
                ad_generation_constants.STATE_KEY_LOGO_IMAGE_URL: matched_config.get("logo_image_uri") or matched_config.get("logo_image_url"),
                ad_generation_constants.STATE_KEY_MAIN_CHARACTER_URL: matched_config.get("main_character_url"),
                ad_generation_constants.STATE_KEY_ASSET_SHEET_URL: matched_config.get("asset_sheet_url")
            }
            
            for key, value in urls_to_validate.items():
                if value and isinstance(value, str) and value.strip():
                    value = value.strip()
                    try:
                        exists, _ = check_asset_exists(value, set())
                        if exists:
                            save_state_property(tool_context, key, value)
                        else:
                            log_message(f"Brand config URL for {key} is unreachable or invalid: {value}", Severity.WARNING)
                    except Exception as e:
                        log_message(f"Error checking brand config URL {value} for {key}: {e}", Severity.WARNING)
            
            config_str = json.dumps(matched_config, indent=2)
            return AgentToolResponse(
                status=Status.SUCCESS,
                detail=f"Successfully retrieved brand identity for '{matched_config['brand_name']}'. The style guidelines and asset URLs have been securely added to your ReadOnly Memory Context. You MUST review them before proceeding.\n\nRetrieved Details:\n{config_str}"
            ).convert_to_agent_response()
            
        else:
            msg = f"No catalog configuration matches the brand '{company_name}'."
            log_message(msg, Severity.INFO)
            return AgentToolResponse(
                status=Status.SUCCESS,
                detail=msg + " You must act in pure generative mode. Invent the visuals from scratch using only the user's instructions. Do not use random fallback URLs."
            ).convert_to_agent_response()

    except Exception as e:
        log_message(f"Error executing retrieve_brand_identity: {e}", Severity.ERROR)
        traceback.print_exc()
        return AgentToolResponse(
            status=Status.ERROR,
            detail=f"An error occurred while attempting to retrieve the brand configuration: {e}"
        ).convert_to_agent_response()
