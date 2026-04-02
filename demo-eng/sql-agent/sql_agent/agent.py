import os
import logging
import warnings

from google.genai import types

from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools import ToolContext, load_artifacts
from google.adk.tools.agent_tool import AgentTool
from google.adk.utils import instructions_utils

from sql_agent import prompts
from sql_agent.sub_agents import db_agent
from sql_agent.sub_agents.bigquery.tools import get_database_settings
from sql_agent.utils import utils_agents, utils_prompts

load_dotenv()

ROOT_AGENT_MODEL = os.getenv("ROOT_AGENT_MODEL")
AGENT_VERSION = os.getenv("AGENT_VERSION")
LOGGING_PREFIX = f"[##SQL_AGENT_ROOT_{AGENT_VERSION}]"

warnings.filterwarnings("ignore", category=UserWarning, module=".*pydantic.*")
logger = logging.getLogger(__name__)

async def _dynamic_instruction_provider(
    context: ReadonlyContext,
) -> str:
    """Dynamically provides instructions to the agent by loading and formatting a prompt.

    This function loads the 'prompt.md' file, replaces placeholders with
    dynamic values (like agent names and configuration keys), and then injects
    the current session state into the prompt before returning it.

    Args:
        context: The read-only context of the agent, providing access to the
            current session state.

    Returns:
        The fully formatted instruction string for the LLM.
    """
    
    prompt = utils_prompts.load_prompt_file_from_calling_agent(
        {
            "CALL_DB_AGENT_TOOL_NAME": _call_db_agent.__name__,
            "SEND_STATUS_UPDATE_TOOL_NAME": _send_status_update.__name__,
        }
    )
    return await instructions_utils.inject_session_state(prompt, context)


async def _send_status_update(message: str, tool_context: ToolContext):
    """Sends a status update message to the user interface.

    This tool is used by the agent to communicate its reasoning, current status,
    or thinking process back to the user in a non-blocking way.

    Args:
        message: The message to be displayed to the user.
        tool_context: The context for the tool, used for updating the state.
    """
    utils_agents.agentspace_print(tool_context, message, LOGGING_PREFIX)


async def _call_db_agent(
    question: str,
    tool_context: ToolContext,
):
    """Tool to call database (nl2sql) agent."""
    print(
        "\n call_db_agent.use_database:"
        f' {tool_context.state["all_db_settings"]["use_database"]}'
    )

    agent_tool = AgentTool(agent=db_agent)

    db_agent_output = await agent_tool.run_async(
        args={"request": question}, tool_context=tool_context
    )
    tool_context.state["db_agent_output"] = db_agent_output
    return db_agent_output


def _setup_before_agent_call(callback_context: CallbackContext):
    """Setup the agent."""

    # setting up database settings in session.state
    if "database_settings" not in callback_context.state:
        db_settings = dict()
        db_settings["use_database"] = "BigQuery"
        callback_context.state["all_db_settings"] = db_settings

    # setting up schema in instruction
    if callback_context.state["all_db_settings"]["use_database"] == "BigQuery":
        callback_context.state["database_settings"] = get_database_settings()
        schema = callback_context.state["database_settings"]["bq_ddl_schema"]

        target_agent = callback_context._invocation_context.agent
        
        if hasattr(target_agent, 'global_instruction'):
            callback_context._invocation_context.agent.global_instruction = (
                prompts.return_instructions_root()
                + f"""

        --------- The BigQuery schema of the relevant data with a few sample rows. ---------
        {schema}

        """
        )
        else:
            callback_context._invocation_context.agent.static_instruction = (
                prompts.return_instructions_root()
                + f"""

        --------- The BigQuery schema of the relevant data with a few sample rows. ---------
        {schema}

        """
        )            


# root_agent = Agent(
root_agent = LlmAgent(
   name="sql_agent",
   model=ROOT_AGENT_MODEL,  
   description="Agent to translate natural language to SQL.",
   instruction=_dynamic_instruction_provider,
   # instruction=prompts.return_instructions_root(),
   tools=[
      _call_db_agent,
      _send_status_update,
      load_artifacts
      ],
    before_agent_callback=_setup_before_agent_call,
    generate_content_config=types.GenerateContentConfig(temperature=0.01),
)

