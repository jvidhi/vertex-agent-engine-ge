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
from typing import List

from pydantic import BaseModel, ValidationError, Field, AliasChoices


class BackupMedia(BaseModel):
    """
    This schema is used to store information on a backup asset
    """
    
    filename: str
    mime_type: str
    url: str = Field(validation_alias=AliasChoices("url", "gcs_uri"))
    description: str
    title: str
    

def parse_backup_media_list(json_string: str) -> List[BackupMedia]:
    """
    Parses a JSON string into a list of BackupMedia objects, 
    with detailed error handling.
    """
    
    backup_media: List[BackupMedia]
    try:
        list_of_dicts = json.loads(json_string)
        backup_media = [BackupMedia(**item) for item in list_of_dicts]
    except json.JSONDecodeError:
        raise TypeError(f"ERROR parsing BackupMedia: The provided string is not valid JSON. String was: {json_string}")
    except ValidationError as e:
        raise ValueError(f"ERROR parsing BackupMedia: The JSON data does not match the BackupMedia schema. Details: {e}")
    except Exception as e:
        raise Exception(f"ERROR parsing BackupMedia: An unexpected error occurred. Details: {e}")
    
    if len(backup_media) < 1:
        raise ValueError(f"ERROR parsing BackupMedia: length is 0. There needs to be at least 1 BackupMedia object in the list. String was: {json_string}")
    
    return backup_media