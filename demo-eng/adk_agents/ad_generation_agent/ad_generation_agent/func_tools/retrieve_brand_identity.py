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
from adk_common.utils.utils_logging import log_message, Severity, log_function_call
from adk_common.utils import utils_gcs
from adk_common.dtos.agent_tool_response import AgentToolResponse, Status
from ad_generation_agent.utils import ad_generation_constants

@log_function_call
def retrieve_brand_identity(company_name: str, tool_context: ToolContext, product_name: Optional[str] = None) -> dict[str, Any]:
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
            
            # --- PRODUCT MATCHING LOGIC ---
            if product_name and "products" in matched_config and isinstance(matched_config["products"], list):
                product_list = matched_config["products"]
                available_products = [p.get("product_name", "") for p in product_list if "product_name" in p]
                log_message(f"Searching for product '{product_name}' among: {available_products}", Severity.INFO)
                
                product_matches = difflib.get_close_matches(
                    product_name.lower(),
                    [p.lower() for p in available_products],
                    n=1,
                    cutoff=0.6
                )
                
                if product_matches:
                    matched_prod_name_lower = product_matches[0]
                    matched_product = next(p for p in product_list if p.get("product_name", "").lower() == matched_prod_name_lower)
                    log_message(f"Product match found: {matched_product.get('product_name')}", Severity.INFO)
                    
                    # Merge product specific fields into the top-level config
                    for k, v in matched_product.items():
                        matched_config[k] = v
                else:
                    log_message(f"No match found for product '{product_name}'. Proceeding with brand-level data only.", Severity.WARNING)
            
            # Save the payload into state for prompt injection on the next turn
            tool_context.state[ad_generation_constants.STATE_KEY_BRAND_CONTEXT_PAYLOAD] = matched_config
            
            from adk_common.utils.utils_state import save_state_property
            from adk_common.utils.utils_agents import check_asset_exists, store_inline_artifact_metadata
            from adk_common.dtos.generated_media import GeneratedMedia
            import mimetypes
            import os

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
                            
                            # Infer mimetype and filename to properly register it in the unified artifact session state
                            mime_type, _ = mimetypes.guess_type(value)
                            if not mime_type:
                                mime_type = "application/octet-stream"
                            
                            filename = os.path.basename(value)
                            if not filename:
                                filename = f"retrieved_{key}"
                                
                            asset_dto = GeneratedMedia(
                                gcs_uri=value,
                                filename=filename,
                                mime_type=mime_type,
                                description=f"Brand Reference Asset: {key}"
                            )
                            # Register this pre-existing canonical asset into the tracking session state
                            store_inline_artifact_metadata(tool_context, asset_dto)
                        else:
                            log_message(f"Brand config URL for {key} is unreachable or invalid: {value}", Severity.WARNING)
                    except Exception as e:
                        log_message(f"Error checking brand config URL {value} for {key}: {e}", Severity.WARNING)
            
            # --- CONTEXT OPTIMIZATION ---
            # Remove the full products array before sending to LLM to save context window
            if "products" in matched_config:
                del matched_config["products"]
            
            config_str = json.dumps(matched_config, indent=2)
            
            detail_msg = f"Successfully retrieved brand identity for '{matched_config['brand_name']}'."
            if product_name and matched_config.get('product_name'):
                detail_msg += f" Successfully isolated data for product '{matched_config['product_name']}'."
            
            return AgentToolResponse(
                status=Status.SUCCESS,
                detail=f"{detail_msg} The style guidelines and asset URLs have been securely added to your ReadOnly Memory Context. You MUST review them before proceeding.\n\nRetrieved Details:\n{config_str}"
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
