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
"""Utility for saving state properties with optional validation."""

from google.adk.tools.tool_context import ToolContext

def save_state_property(context: ToolContext, key: str, value: str) -> None:
    """Saves a value to the session state.
    
    Args:
        context (ToolContext): The tool context containing the state dictionary.
        key (str): The state key under which to save the value.
        value (str): The value to save.
    """
    if value and isinstance(value, str) and value.strip():
        # Save to state
        context.state[key] = value.strip()


def get_state_property(context: ToolContext, key: str, default_value: str | None = None) -> str | None:
    """Retrieves a value from the session state.
    
    Args:
        context (ToolContext): The tool context containing the state dictionary.
        key (str): The state key to retrieve.
        default_value (str | None): The value to return if the key is not found.
        
    Returns:
        str | None: The retrieved value, or the default_value if the key is not found.
    """
    return context.state.get(key, default_value)