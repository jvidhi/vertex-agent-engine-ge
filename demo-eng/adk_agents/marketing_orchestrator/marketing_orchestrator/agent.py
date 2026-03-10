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

"""
root_marketing_coordinator: Orchestrates specialized sub-agents for marketing.

This agent acts as the primary user-facing coordinator. It analyzes user
requests and delegates tasks to the appropriate sub-agent, such as
the marketing_plan_agent for strategy or the genmedia_agent for
all media generation, editing, and video creation.
"""

import json
from typing import Optional

from adk_common.utils import utils_agents, utils_gcs, utils_prompts
from adk_common.utils.env_loader import load_env_cascade
from adk_common.utils.constants import (get_optional_env_var,
                                        get_required_env_var)

# Load environment variables from .env files (Current Agent + Dependencies)
load_env_cascade(__file__, dependency_paths=[
    "../genmedia_agent",
    "../marketing_plan_agent",
    "../ad_generation_agent"
])
from ad_generation_agent.agent import root_agent as ad_generation_agent
from genmedia_agent.agent import root_agent as genmedia_agent
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool
from google.adk.tools.agent_tool import AgentTool
from google.adk.utils import instructions_utils
from marketing_plan_agent.agent import root_agent as marketing_plan_agent

IS_DEBUG_ON = get_optional_env_var("IS_DEBUG_ON", 'False').lower() in ('true', '1', 't')

# --- Agent Configuration ---
LLM_GEMINI_MODEL_ROOT = get_required_env_var("LLM_GEMINI_MODEL_ROOT")
AGENT_VERSION = get_required_env_var("AGENT_VERSION")
DEMO_COMPANY_NAME = get_optional_env_var("DEMO_COMPANY_NAME", "our company")

AGENT_NAME_MARKETING_ORCHESTRATOR = "marketing_orchestrator"
LOGGING_PREFIX = f"[##MARKETING_AGENT_ROOT_{AGENT_VERSION}]"

ROOT_AGENT_DESCRIPTION = f"""
The {DEMO_COMPANY_NAME} Agent is an AI Agent uniquely designed to accelerate time to value at every stage of the marketing process.
This AI Agent can streamline each step of the marketing process - from planning to execution. It coordinates specialized agents to:
- Identify audience segments
- Craft targeted marketing campaigns
- Generate and edit images, storyboards, and videos
"""

DEBUG_INSTRUCTIONS = (
    ""
    if not IS_DEBUG_ON
    else """## DEBUG MODE
You are currently running in debug mode. Before any function call, when deciding on a new step or when receiving a response (successful or not), call `Debug` tool with a descriptive `message`
"""
)


async def _dynamic_instruction_provider(
    context: ReadonlyContext,
) -> str:
    """Dynamically provides instructions to the agent by loading and formatting a prompt."""

    prompt = utils_prompts.load_prompt_file_from_calling_agent(
        {
            "AGENT_VERSION": AGENT_VERSION,
            "DEMO_COMPANY_NAME": DEMO_COMPANY_NAME,
            "GENMEDIA_AGENT_NAME": genmedia_agent.name,
            "MARKETING_PLAN_AGENT_NAME": marketing_plan_agent.name,
            "AD_GENERATION_AGENT_NAME": ad_generation_agent.name,
            "GCS_AUTHENTICATED_DOMAIN": utils_gcs.GCS_AUTHENTICATED_DOMAIN,
            "DEBUG_INSTRUCTIONS": DEBUG_INSTRUCTIONS,
        }
    )
    return await instructions_utils.inject_session_state(prompt, context)


async def Debug(message: str, tool_context: ToolContext):
    """A tool for printing debug messages. Only use if in debug mode."""
    utils_agents.geminienterprise_print(
        context=tool_context,
        message=message,
    )
    print(f"{LOGGING_PREFIX} ##DEBUG: {message}")


def _before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Logs the request details before calling the model."""
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    current_state = json.dumps(callback_context.state.to_dict())

    print(f"{LOGGING_PREFIX} Before Model Callback")
    print(f"{LOGGING_PREFIX} Starting Agent: {agent_name} (Inv: {invocation_id})")
    print(f"{LOGGING_PREFIX} Current State: {current_state}")
    print(f"{LOGGING_PREFIX} Request: {utils_agents.stringify_llm_request(llm_request)}")
    print(f"{LOGGING_PREFIX} User Content: {callback_context.user_content}")
    return None  # Allow the model call to proceed


def _after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """Logs the response details after the model call."""
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    current_state = json.dumps(callback_context.state.to_dict())

    print(f"{LOGGING_PREFIX} After Model Callback")
    print(f"{LOGGING_PREFIX} Current State: {current_state}")
    print(f"{LOGGING_PREFIX} LLM Response: {utils_agents.stringify_llm_response(llm_response)}")
    print(f"{LOGGING_PREFIX} Exiting Agent: {agent_name} (Inv: {invocation_id})")
    return None  # Allow the model call to proceed


# --- Define Agent Tools ---
# This agent's tools are the other agents.
agent_tools = [
    AgentTool(agent=marketing_plan_agent),
    AgentTool(agent=genmedia_agent),
    AgentTool(agent=ad_generation_agent),
    LoadArtifactsTool,
]

if IS_DEBUG_ON:
    agent_tools.append(Debug)

# --- Create the Agent ---
marketing_orchestrator = LlmAgent(
    model=LLM_GEMINI_MODEL_ROOT,
    name="marketing_orchestrator",
    description=ROOT_AGENT_DESCRIPTION,
    instruction=_dynamic_instruction_provider,
    tools=agent_tools,
    before_model_callback=_before_model_callback,
    after_model_callback=_after_model_callback,
)

root_agent = marketing_orchestrator