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

"""marketing_create_agent: for creating marketing strategies"""

from typing import Optional

from adk_common.utils import utils_agents, utils_gcs, utils_prompts
from adk_common.utils.constants import (get_optional_env_var,
                                               get_required_env_var)
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool
from google.adk.utils import instructions_utils

LLM_GEMINI_MODEL_MARKETINGPLAN = get_required_env_var("LLM_GEMINI_MODEL_MARKETINGPLAN")
AGENT_VERSION = get_required_env_var("AGENT_VERSION")

MARKETING_PLAN_AGENT_OUTPUT_KEY = "MARKETING_PLAN_AGENT_OUTPUT"
LOGGING_PREFIX = f"[##MARKETING_AGENT_MARKETINGPLAN_{AGENT_VERSION}]"

MARKETING_PLAN_AGENT_DESCRIPTION = """
Creative AI assistant specializing in crafting comprehensive and effective marketing strategies. AI agent for all marketing-related questions and needs.
Objectives: answer marketing-related questions and generate tailored marketing strategies based on the user's input, designed to achieve their specific business or project goals.
"""


async def _dynamic_instruction_provider(context: ReadonlyContext) -> str:
    prompt = utils_prompts.load_prompt_file_from_calling_agent()
    return await instructions_utils.inject_session_state(prompt, context)


def _before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    llm_prompt = utils_agents.stringify_llm_request(llm_request)

    print(f"{LOGGING_PREFIX} Before Model Callback")
    print(f"{LOGGING_PREFIX} Starting Agent: {agent_name} (Inv: {invocation_id})")
    print(f"{LOGGING_PREFIX} LLM Prompt: {llm_prompt}")

    return None  # Allow the model call to proceed


def _after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id

    print(f"{LOGGING_PREFIX} After Model Callback")
    print(f"{LOGGING_PREFIX} Exiting Agent: {agent_name} (Inv: {invocation_id})")
    return None  # Allow the model call to proceed


marketing_plan_agent = LlmAgent(
    model=LLM_GEMINI_MODEL_MARKETINGPLAN,
    name="marketing_planner_agent",
    description=MARKETING_PLAN_AGENT_DESCRIPTION,
    instruction=_dynamic_instruction_provider,
    output_key=MARKETING_PLAN_AGENT_OUTPUT_KEY,
    before_model_callback=_before_model_callback,
    after_model_callback=_after_model_callback,
    tools=[LoadArtifactsTool],
)

root_agent = marketing_plan_agent