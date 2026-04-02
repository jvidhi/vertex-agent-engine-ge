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


from enum import Enum
from typing import Any, List, Literal

from pydantic import BaseModel, field_validator
from .generated_media import GeneratedMedia


class Status(Enum):
    SUCCESS = "success"
    ERROR = "error"


class AgentToolResponse(BaseModel):
    """
    This schema is used to represent a Request tp RetrieveAssetAgent
    """
    
    status: Literal["success", "error"] | Status
    detail: str | None = None
    
    
    @field_validator('status', mode='before')
    @classmethod
    def convert_enum_to_str(cls, v: Any) -> Any:
        """
        Coerces a Status enum into its string value before validation.
        """
        possible_values = [member.value for member in Status]
        if isinstance(v, Status):
            return v.value  # Return "success" or "error"
        elif v in possible_values:
            return v  # Return "success" or "error"
        else:
            raise ValueError(f"Invalid Status value: {v}. Possible values: {possible_values}")
    
    
    def convert_to_agent_response(self):
        return {
            "status": self.status,
            "detail": self.detail,
        }


class AgentToolResponseGenMedia(AgentToolResponse):
    """
    Extends AgentToolResponse to include a list of generated media.
    """
    generated_media: List[GeneratedMedia]
    
    def convert_to_agent_response(self):
        processed_media = [
            media_item.to_obj_sans_bytes() for media_item in self.generated_media
        ]
            
        return {
            "status": self.status,
            "detail": self.detail,
            "generated_media": processed_media
        }